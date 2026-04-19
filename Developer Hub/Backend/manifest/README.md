# `manifest/`

**Purpose.** Source assets for the **Fabric workload manifest package** — the
`.nupkg` that registers your workload (and its item types) with Fabric. This
directory is **build input**, not Python runtime source (so it lives outside
`src/`).

## Files

| File | Contents |
|---|---|
| `WorkloadManifest.xml` | Root workload definition. Template — `${WORKLOAD_NAME}`, `${CLIENT_ID}`, `${AUDIENCE}` are substituted from `Developer Hub/.env` at package-build time by [`../tools/manifest_package_generator.py`](../tools/manifest_package_generator.py). |
| `AgentHubItem.xml` | Item-type definition for `${WORKLOAD_NAME}.AgentHubItem`. Same template substitution. |
| `WorkloadDefinition.xsd` / `ItemDefinition.xsd` / `CommonTypesDefinitions.xsd` | XML schemas published by Microsoft; used by validators in [`Developer Hub/tools/validation/`](../../../tools/validation/). |
| `ManifestPackageDebug.nuspec` / `ManifestPackageRelease.nuspec` | NuGet package metadata for the two build flavours. |

## Build flow

```
WorkloadManifest.xml + AgentHubItem.xml  ──┐
                                    ├── manifest_package_generator.py → bin/Debug/<workload>.<ver>.nupkg
.env (placeholder values)        ──┘                                          │
                                                                              ▼
                                                        served by Frontend, registered with Fabric DevGateway
```
