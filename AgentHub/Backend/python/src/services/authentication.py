import logging

from jose import  jwt, JWTError
from jose.exceptions import JWTClaimsError, ExpiredSignatureError, JWTError
from typing import Optional, List, Dict, Any
import msal

from msal.exceptions import MsalServiceError
from constants.http_constants import AuthorizationSchemes
from constants.environment_constants import EnvironmentConstants
from services.configuration_service import get_configuration_service
from constants.workload_scopes import WorkloadScopes
from models.authentication_models import SubjectAndAppToken, TokenVersion, AuthorizationContext, Claim
from exceptions.exceptions import AuthenticationException, AuthenticationUIRequiredException
from services.open_id_connect_configuration import OpenIdConnectConfigurationManager
from constants.api_constants import ApiConstants

logger = logging.getLogger(__name__)

class AuthenticationService:
    def __init__(self, openid_manager: OpenIdConnectConfigurationManager):
        self.logger = logging.getLogger(__name__)
        self.openid_manager = openid_manager
        config_service = get_configuration_service()
        self.publisher_tenant_id = config_service.get_publisher_tenant_id()
        self.audience = config_service.get_audience()
        self.client_id = config_service.get_client_id()
        self.client_secret = config_service.get_client_secret()
        self._msal_apps = {}

        
        # Default scopes for SubjectAndApp token authentication
        self.subject_and_app_auth_allowed_scopes = [WorkloadScopes.FABRIC_WORKLOAD_CONTROL]
        
        # Create MSAL confidential client application
        self.authority_template = f"{EnvironmentConstants.AAD_INSTANCE_URL}/{{tenant_id}}"
        default_authority = f"{EnvironmentConstants.AAD_INSTANCE_URL}/organizations"
        self.app = None
        if self.client_id and self.client_secret and self.publisher_tenant_id:
            self._msal_apps[default_authority] = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=default_authority
            )
            self.logger.info("MSAL Confidential Client Application initialized")
        else:
            self.logger.warning("Missing ClientId or ClientSecret in configuration. MSAL client not initialized.")

    def _get_msal_app(self, tenant_id: str) -> msal.ConfidentialClientApplication:
        """Gets or creates an MSAL app for the specified tenant."""
        authority = f"{EnvironmentConstants.AAD_INSTANCE_URL}/{tenant_id}"
        
        if authority not in self._msal_apps:
            self._msal_apps[authority] = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                authority=authority,
                client_credential=self.client_secret
            )
        
        return self._msal_apps[authority]

    
    
    async def authenticate_control_plane_call(
        self, 
        auth_header: Optional[str],
        tenant_id: Optional[str] = None,
        require_subject_token: bool = True,
        require_tenant_id_header: bool = True
    ) -> AuthorizationContext:
        """
        Authenticate a control plane API call using the authorization header.
        
        This is called during item create/update/delete/get/Jobs operations.
        """
        self.logger.info("Authenticating control plane call")
        
        if not auth_header:
            self.logger.error("Missing or invalid Authorization header")
            raise AuthenticationException("Missing or invalid Authorization header")
            
        if require_tenant_id_header and not tenant_id:
            self.logger.error("tenant_id header is missing")
            raise AuthenticationException("tenant_id header is missing")
            
        # Parse the tokens
        try:
            subject_and_app_token = SubjectAndAppToken.parse(auth_header)
        except AuthenticationException as e:
            self.logger.error(f"Failed to parse SubjectAndAppToken: {str(e)}")
            raise
            
        # Create authorization context based on parsed tokens
        auth_context = await self._authenticate(
            tenant_id, 
            subject_and_app_token,
            self.subject_and_app_auth_allowed_scopes,
            require_subject_token,
            require_tenant_id_header
        )
        
        return auth_context
        
    async def authenticate_data_plane_call(
        self,
        auth_header: Optional[str],
        allowed_scopes: List[str],
        tenant_id: Optional[str] = None
    ) -> AuthorizationContext:
        """
        Authenticate a data plane API call using the authorization header.
        
        This is called for custom API operations like getting supported operators.
        
        Args:
            auth_header: The authorization header from the request
            allowed_scopes: List of scopes required for the operation
            tenant_id: Optional tenant ID from the request header
            
        Returns:
            AuthorizationContext with user and tenant information
        """
        self.logger.info(f"Authenticating data plane call with scopes: {allowed_scopes}")
        
        if not auth_header or not auth_header.startswith("Bearer "):
            self.logger.error("Missing or invalid Authorization header")
            raise AuthenticationException("Missing or invalid Authorization header")
            
        token = auth_header[len(AuthorizationSchemes.BEARER):].strip()
        auth_context = await self._authenticate_bearer(token, allowed_scopes)
        return auth_context
    
    async def get_access_token_on_behalf_of(
        self,
        auth_context: AuthorizationContext,
        scopes: List[str]
    ) -> str:
        """Get an access token using OBO flow."""
        self.logger.info(f"Getting access token for scopes: {', '.join(scopes)}")
        
        if not auth_context.original_subject_token:
            self.logger.error("No original_subject_token in AuthorizationContext for OBO flow.")
            raise AuthenticationException("OBO flow requires an original subject token.")
        
        if not self.client_id or not self.client_secret: # Check if base MSAL config is present
            self.logger.error("MSAL client_id or client_secret not configured, cannot perform OBO flow.")
            raise AuthenticationException("MSAL client not configured for OBO flow.")

        if not auth_context.tenant_object_id:
            self.logger.error("TenantObjectId missing in AuthorizationContext for OBO flow. Cannot determine authority.")
            raise AuthenticationException("Cannot determine tenant authority for OBO flow.")

        obo_app = self._get_msal_app(auth_context.tenant_object_id)
        self.logger.debug(f"OBO MSAL app configured with authority: {auth_context.tenant_object_id}")

        try:
            result = obo_app.acquire_token_on_behalf_of(
                user_assertion=auth_context.original_subject_token,
                scopes=scopes
            )

        except Exception as e:
            self.logger.error(f"MSAL OBO acquire_token_on_behalf_of call failed unexpectedly: {str(e)}", exc_info=True)
            raise AuthenticationException(f"OBO token acquisition failed: {str(e)}")
            
        if "error" in result:
            error_code = result.get("error")
            error_description = result.get("error_description", "")
            self.logger.error(f"Error in OBO token acquisition: {error_code}: {error_description}")
            # Handle consent required error
            if error_code in ["interaction_required", "consent_required", "invalid_grant"] or result.get("suberror") == "conditional_access":
                claims_challenge = result.get("claims")
                py_ex = AuthenticationUIRequiredException(error_description)
                if claims_challenge:
                    py_ex.add_claims_for_conditional_access(claims_challenge)
                if error_code == "consent_required" or "consent_required" in error_description.lower():
                    py_ex.add_scopes_to_consent(scopes)
                self.logger.warning(f"OBO flow requires UI interaction: {error_code}. Claims: {claims_challenge}, Scopes: {scopes if 'consent_required' in error_description.lower() or error_code == 'consent_required' else 'N/A'}")
                raise py_ex
            raise AuthenticationException(f"Error acquiring token: {error_code}")
            
        if "access_token" not in result:
            self.logger.error("Access token not found in OBO result")
            raise AuthenticationException("Access token not found in OBO result")
            
        self.logger.info(f"OBO flow successful for user {auth_context.object_id}.")
        return result["access_token"]
        
    async def build_composite_token(
        self,
        auth_context: AuthorizationContext,
        scopes: List[str]
    ) -> str:
        """Build a composite token for making calls to Fabric APIs."""
        self.logger.info(f"Building composite token for scopes: {', '.join(scopes)}")
        
        # Get OBO token for Fabric
        token_obo = await self.get_access_token_on_behalf_of(auth_context, scopes)
        
        # Get service-to-service token
        service_principal_token = await self.get_fabric_s2s_token()
        
        # Generate SubjectAndAppToken authorization header
        return SubjectAndAppToken.generate_authorization_header_value(token_obo, service_principal_token)
    
    async def get_fabric_s2s_token(self) -> str:
        """Get a service-to-service token for Fabric."""
        self.logger.info("Acquiring Fabric S2S token")         
        try:
            # Request token with default scope
            scopes = [f"{EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID}/.default"]

            publisher_authority = f"{EnvironmentConstants.AAD_INSTANCE_URL}/{self.publisher_tenant_id}"
            app = self._get_msal_app(self.publisher_tenant_id)
            try:
                result = app.acquire_token_for_client(scopes=scopes)
            except MsalServiceError as e:
                self.logger.error(f"MSAL exception: {str(e)}")
                raise AuthenticationException(f"MSAL exception: {str(e)}")
            
            if "error" in result:
                error_code = result.get("error")
                error_description = result.get("error_description", "")
                self.logger.error(f"MSAL exception: {error_code}: {error_description}")
                raise AuthenticationException(f"MSAL exception: {error_code}")
                
            return result["access_token"]
            
        except AuthenticationException:
            raise
        except Exception as e:
            self.logger.error(f"An error occurred: {str(e)}")
            raise Exception(f"An error occurred: {str(e)}")

    async def _authenticate(
        self,
        tenant_id: Optional[str],
        subject_and_app_token: SubjectAndAppToken,
        allowed_scopes: List[str],
        require_subject_token: bool = True,
        require_tenant_id_header: bool = True
    ) -> AuthorizationContext:
        """Authenticate using SubjectAndAppToken."""
        if require_tenant_id_header and not tenant_id:
            self.logger.error("tenant_id header is missing")
            raise AuthenticationException("tenant_id header is missing")

        app_token_claims = await self._validate_app_token(subject_and_app_token.app_token)
        app_token_version = self._get_token_version(app_token_claims)
        
        # Check app ID claim based on token version
        app_id_claim = "appid" if app_token_version == TokenVersion.V1 else "azp"
        app_token_app_id = self._validate_claim_one_of_values(
        app_token_claims, 
        app_id_claim,
        [EnvironmentConstants.FABRIC_BACKEND_APP_ID, 
         EnvironmentConstants.FABRIC_CLIENT_FOR_WORKLOADS_APP_ID],
        "app-only token must belong to Fabric BE or Fabric client for workloads"
    )
        
        # Validate app token belongs to publisher tenant
        self._validate_claim_value(app_token_claims, "tid", self.publisher_tenant_id, 
                                "app token must be in the publisher's tenant")
        
        # Handle missing subject token
        if not subject_and_app_token.subject_token:
            if require_subject_token:
                self.logger.error("subject token is missing")
                raise AuthenticationException("SubjectAndAppToken is missing subject token")
            
            # Create context without subject info
            if require_tenant_id_header:
                return AuthorizationContext(tenant_object_id=tenant_id)
            else:
                return AuthorizationContext()
        
        # Validate subject token
        subject_claims = await self._validate_subject_token(subject_and_app_token.subject_token, tenant_id)
        subject_token_version = self._get_token_version(subject_claims)
        
        # Validate app IDs match between tokens
        subject_app_id_claim = "appid" if subject_token_version == TokenVersion.V1 else "azp"
        self._validate_claim_value(subject_claims, subject_app_id_claim, app_token_app_id, 
                                 "subject and app tokens should belong to same application")
        
        # Validate tenant ID
        self._validate_claim_value(subject_claims, "tid", tenant_id, "subject tokens must belong to the subject's tenant")
        
        # Validate scopes
        self._validate_any_scope(subject_claims, allowed_scopes)
        
        # Create context with subject info - properly set fields that exist in the model
        auth_context = AuthorizationContext(
            original_subject_token=subject_and_app_token.subject_token,
            tenant_object_id=tenant_id,  # Use tenant_object_id instead of tenant_id
            claims=subject_claims
        )
        
        return auth_context
    
    def _validate_claim_one_of_values(self, claims: List[Claim], claim_name: str, 
                                  expected_values: List[str], error_message: str) -> str:
        """Validate a claim exists and matches one of the expected values."""
        claim_value = self._validate_claim_exists(claims, claim_name, 
                                                f"Missing required claim: {claim_name}")
        
        if claim_value not in expected_values:
            self.logger.error(
                f"{error_message}: claim '{claim_name}' has value '{claim_value}', "
                f"expected one of: {expected_values}"
            )
            raise AuthenticationException(error_message)
            
        return claim_value

    async def _authenticate_bearer(self, token: str, allowed_scopes: List[str]) -> AuthorizationContext:
        """Authenticate a bearer token"""
        claims = await self._validate_aad_token_common(token, is_app_only=False, expected_tenant_id_for_issuer=None)
        
        # Extract tenant ID
        tenant_id = self._validate_claim_exists(claims, "tid", "access tokens should have this claim")
        
        # Validate scopes
        self._validate_any_scope(claims, allowed_scopes)
        
        # Create context with subject info properly
        auth_context = AuthorizationContext(
            original_subject_token=token,
            tenant_object_id=tenant_id, 
            claims=claims
        )
        
        return auth_context
    
    def get_expected_issuer(self, oidc_config:OpenIdConnectConfigurationManager ,token_version: TokenVersion, tenant_id: str) -> str:
        """Get the expected issuer for the token version and tenant ID."""
        expected_issuer = None
        if token_version == TokenVersion.V1:
            try:
                expected_issuer = oidc_config.issuer_configuration.format(tenantid=tenant_id)
            except KeyError:
                logger.error(f"Issuer configuration:{oidc_config.issuer_configuration} missing tenantid placeholder 'tenantid'")
                raise AuthenticationException("Issuer configuration missing tenantid placeholder")
        elif token_version == TokenVersion.V2:
            expected_issuer = f"{EnvironmentConstants.AAD_INSTANCE_URL}/{tenant_id}/v2.0"
        else:
            self.logger.error(f"Unsupported token version: {token_version}")
            raise AuthenticationException(f"Unsupported token version: {token_version}")
        return expected_issuer

    def _get_token_version(self, claims: List[Claim]) -> str:
        """Gets the token version from claims."""
        version = self._validate_claim_exists(claims, "ver", "access tokens should have version claim")
        if version == "1.0":
            return TokenVersion.V1
        elif version == "2.0":
            return TokenVersion.V2
        else:
            raise AuthenticationException(f"Unsupported token version: {version}")
        
    def _get_excpected_audience(self, token_version: TokenVersion) -> str:
        """Get the expected audience based on token version."""
        return self.audience if token_version == TokenVersion.V1 else self.client_id
           
    async def _validate_aad_token_common(self, token: str, is_app_only: bool, expected_tenant_id_for_issuer: Optional[str]) -> Dict[str, Any]:
        """
        Validate common properties of an AAD token (signature, lifetime, audience, issuer).
        Returns the decoded claims as a dictionary.
        """
        self.logger.debug(f"Validating AAD token. is_app_only: {is_app_only}, expected_tenant_id_for_issuer: {expected_tenant_id_for_issuer}")
        try:
            unverified_header = jwt.get_unverified_header(token)
            unverified_claims_dict = jwt.get_unverified_claims(token)

            unverified_claims_list = [Claim(type=k, value=v) for k, v in unverified_claims_dict.items()]
            # Extract tenant ID from claims
            tenant_id = self._validate_claim_exists(unverified_claims_list, "tid", "access tokens should have 'tid' claim")
            
            if not tenant_id:
                self.logger.error("Token is missing 'tid' claim.")
                raise AuthenticationException("Token is missing 'tid' claim.")
            
            # Get token version for issuer and audience validation
            token_version = self._get_token_version(unverified_claims_list)
            self.logger.debug(f"Token version: {token_version}")

            # Get OpenID Connect configuration for signing keys
            oidc_config = await self.openid_manager.get_configuration_async()

            signing_key = None
            for key in oidc_config.signing_keys:
                if key.get("kid") == unverified_header.get("kid"):
                    signing_key = key
                    break

            if not signing_key:
                logger.error("Token signing key not found")
                raise AuthenticationException("Token signing key not found")
            
            expected_issuer = self.get_expected_issuer(oidc_config, token_version, tenant_id)
            if not expected_issuer:
                self.logger.error("Expected issuer not found")
                raise AuthenticationException("Expected issuer not found")
            
            expected_audience = self._get_excpected_audience(token_version)
            self.logger.debug(f"Expected audience: {expected_audience}")

            # Validate token fully
            decoded_payload = jwt.decode(
                token,
                key=signing_key,
                algorithms=[unverified_header.get("alg", "RS256")],
                audience=expected_audience,
                issuer=expected_issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "leeway": 60,  # 1 minute leeway for time checks
                }
            )

            claims = [Claim(type=k, value=v) for k, v in decoded_payload.items()]
            self.logger.debug(f"Token validated successfully. Claims: {decoded_payload}")

            app_id_claim = "appid" if token_version == TokenVersion.V1 else "azp"
            self._validate_claim_exists(claims, app_id_claim, f"access tokens should have {app_id_claim} claim")

            self._validate_app_only(claims, is_app_only)
            self.logger.info("AAD token validation successful")
            return claims
        
        except ExpiredSignatureError:
            self.logger.error("Token has expired")
            raise AuthenticationException("Token has expired")
        except JWTClaimsError as e:
            if "Invalid audience" in str(e):
                token_audience_from_unverified = unverified_claims_dict.get("aud") if 'unverified_claims_dict' in locals() else "N/A (unverified claims not available)"
                expected_audiences_for_log = expected_audience if 'valid_audiences' in locals() else "N/A (expected audiences not available)"
                error_message = f". Expected: {expected_audiences_for_log}, Got: {token_audience_from_unverified}"
            
            self.logger.error(error_message)
            self.logger.error(f"Token has invalid claims: {str(e)}")
            raise AuthenticationException(f"Invalid token claims: {str(e)}")
        except JWTError as e:
            self.logger.error(f"JWT validation failed: {str(e)}")
            raise AuthenticationException(f"Token validation failed: {str(e)}")
        except Exception as e:
            self.logger.error(f"Token validation failed: {str(e)}")
            raise AuthenticationException(f"Token validation failed: {str(e)}")
        
    async def _validate_app_token(self, token: str) -> Dict[str, Any]:
        """
        Validate an app token (app-only) with publisher tenant validation.
        """
        return await self._validate_aad_token_common(
            token, 
            is_app_only=True, 
            expected_tenant_id_for_issuer=self.publisher_tenant_id
        )

    async def _validate_subject_token(self, token: str, tenant_id: str) -> Dict[str, Any]:
        """
        Validate a subject token (delegated) with the user's tenant.
        """
        return await self._validate_aad_token_common(
            token, 
            is_app_only=False, 
            expected_tenant_id_for_issuer=tenant_id
        )

    def _validate_claim_value(self, claims: List[Claim], claim_name: str, expected_value: str = None, 
                            error_message: str = None) -> str:
        """Validate a claim exists and optionally matches expected value."""
        claim_value = self._validate_claim_exists(claims, claim_name, f"Missing required claim: {claim_name}")
        
        if expected_value is not None and str(claim_value) != expected_value:
            error_msg = error_message or f"Claim {claim_name} has incorrect value"
            self.logger.error(f"{error_msg}: expected '{expected_value}', got '{claim_value}'")
            raise AuthenticationException(error_msg)
            
        return claim_value

    def _validate_claim_exists(self, claims: List[Claim], claim_name: str, error_message: str) -> str:
        """Validate a claim exists and return its value."""
        for claim in claims:
            if claim.type == claim_name:
                return claim.value
                
        self.logger.error(f"Missing claim {claim_name}: {error_message}")
        raise AuthenticationException(f"Missing claim {claim_name}: {error_message}")

    def _validate_no_claim(self, claims: List[Claim], claim_name: str, error_message: str) -> None:
        """Validate a claim does not exist."""
        for claim in claims:
            if claim.type == claim_name:
                self.logger.error(f"Unexpected claim exists: claimType='{claim_name}', reason='{error_message}', actualValue={claim.value}")
                raise AuthenticationException("Unexpected token format")

    def _validate_app_only(self, claims: List[Claim], is_app_only: bool) -> None:
        """Validate that the token is either app-only or delegated based on claims."""
        if is_app_only:
            self._validate_claim_value(claims, "idtyp", "app", "expecting an app-only token")
            self._validate_claim_exists(claims, "oid", "app-only tokens should have oid claim in them")
            self._validate_no_claim(claims, "scp", "app-only tokens should not have this claim")
        else:
            self._validate_no_claim(claims, "idtyp", "delegated tokens should not have this claim")
            self._validate_claim_exists(claims, "scp", "delegated tokens should have this claim")
    
    def _extract_scopes_from_claims(self, claims: List[Claim]) -> List[str]:
        """Extract all scopes from both delegated (scp) and application (roles) claims."""
        token_scopes = []
        
        # Extract delegated permissions from scp claim
        for claim in claims:
            if claim.type == "scp":
                scopes_str = claim.value if claim.value else ""
                if isinstance(scopes_str, str):
                    token_scopes.extend([s.strip() for s in scopes_str.split()])

        for claim in claims:
            if claim.type == "roles":
                roles = claim.value if claim.value else []
                if isinstance(roles, list):
                    token_scopes.extend(roles)
                elif isinstance(roles, str):
                    token_scopes.append(roles)
                    
        return token_scopes

    def _validate_any_scope(self, claims: List[Claim], allowed_scopes: List[str]) -> None:
        """Validate that the token has at least one of the allowed scopes."""
        token_scopes = self._extract_scopes_from_claims(claims)

        # Check if any allowed scope is present in token scopes
        if not any(scope in token_scopes for scope in allowed_scopes):
            allowed_scopes_str = ", ".join(allowed_scopes)
            token_scopes_str = ", ".join(token_scopes) if token_scopes else "none"
            error_message = "Workload's Entra ID application is missing required scopes"
            self.logger.error(f"{error_message}. Required: [{allowed_scopes_str}], Found: [{token_scopes_str}]")
            raise AuthenticationException(error_message)
    
        self.logger.debug(f"Scope validation successful. Required: {allowed_scopes}, Found: {token_scopes}")


def get_authentication_service() -> AuthenticationService:
    """Get the singleton AuthenticationService instance."""
    from core.service_registry import get_service_registry
    service_registry = get_service_registry()
    if not service_registry.has(AuthenticationService):
        if not hasattr(get_authentication_service, "instance"):
            raise RuntimeError(
                "AuthenticationService not initialized. "
                "Ensure the application startup has completed."
            )
        return get_authentication_service.instance
    return service_registry.get(AuthenticationService)