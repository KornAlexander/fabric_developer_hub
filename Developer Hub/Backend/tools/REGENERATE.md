# Regenerating fabric_api/

`openapi.yaml` is the **Fabric → Workload contract** published by Microsoft. It
defines the endpoints Fabric calls into this workload (item lifecycle, jobs,
endpoint resolution). The Python server stubs under
`src/fabric_api/apis/` and Pydantic models under `src/fabric_api/models/`
are generated from it using [openapi-generator](https://openapi-generator.tech/).

## What is and isn't generated

| Path | Source | Edit by hand? |
|---|---|---|
| `src/fabric_api/apis/`        | **Generated** | No — regenerate instead |
| `src/fabric_api/models/`      | **Generated** | No — regenerate instead |
| `src/fabric_api/impl/`        | Generated stubs, then **hand-edited** business logic | Yes |
| `src/impl/*_controller.py`    | **Hand-written** Developer Hub routes (not part of the contract) | Yes |

## When to regenerate

Only when Microsoft publishes an updated workload OpenAPI spec. The custom
Developer Hub endpoints (`agenthub_controller.py`, `github_chat_controller.py`,
`lakehouse_controller.py`, `onelake_controller.py`) are **not** in this spec
and must not be added to it.

## How to regenerate

```bash
# From Developer Hub/Backend
docker run --rm \
  -v "$PWD:/local" \
  openapitools/openapi-generator-cli generate \
    -i /local/openapi.yaml \
    -g python-fastapi \
    -o /local/_regen \
    --additional-properties=packageName=fabric_api,fastapiImplementationPackage=fabric_api.impl

# Then sync only the safe (fully generated) directories:
rsync -a --delete _regen/src/fabric_api/apis/   src/fabric_api/apis/
rsync -a --delete _regen/src/fabric_api/models/ src/fabric_api/models/

# Review _regen/src/fabric_api/impl/ by hand — do NOT overwrite.
rm -rf _regen
```

After regeneration, run the test suite:
```bash
python run_tests.py
```
