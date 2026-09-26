"""
Admin webui routes for managing OIDC providers - list, create, edit, delete.
Gated by require_admin_session (see admin_router.py)
"""

from typing import Annotated
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from Pytre_Core.oidc_providers import OidcProvider
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/sso")


class OidcProviderForm(BaseModel):
    label: str = ""
    issuer: str
    client_id: str
    client_secret: str = ""
    scopes: str = "openid"
    username_claim: str = "preferred_username"
    public_base_url: str = ""
    enabled: bool = False

    def apply_to(self, provider: OidcProvider) -> None:
        """Copies the submitted values onto `provider`. An empty client_secret keeps the current
        one - a provider can't be re-saved without retyping a secret it already has."""
        provider.label = self.label.strip()
        provider.issuer = self.issuer.strip()
        provider.client_id = self.client_id.strip()
        if self.client_secret:
            provider.client_secret = self.client_secret
        provider.scopes = self.scopes.strip() or "openid"
        provider.username_claim = self.username_claim.strip() or "preferred_username"
        provider.public_base_url = self.public_base_url.strip().rstrip("/")
        provider.enabled = self.enabled


def _form_context(provider: OidcProvider) -> dict:
    return {"provider": provider}


# ------------------------------------------------------------------------------------------
# Obtain list
# ------------------------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def sso_list(
    request: Request,
    state: ServerState = Depends(get_server_state),
    msg: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    providers = state.app_ctx.oidc_providers.get_all_providers(reload=False)
    context = {"providers": providers, "msg": msg, "error": error}
    return templates.TemplateResponse(request, "admin/oidc_providers_list.html", context)


@router.post("/reload", response_class=RedirectResponse)
def sso_reload(state: ServerState = Depends(get_server_state)) -> RedirectResponse:
    try:
        providers = state.app_ctx.oidc_providers.get_all_providers(reload=True)
    except Exception as e:
        msg = f"Reload failed: {quote_plus(str(e))}"
        return RedirectResponse(url=f"/admin/sso?error={msg}", status_code=status.HTTP_303_SEE_OTHER)

    msg = f"OIDC providers reloaded ({len(providers)} loaded)"
    return RedirectResponse(url=f"/admin/sso?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Create / edit (shared form + save logic)
# ------------------------------------------------------------------------------------------
@router.get("/new", response_class=HTMLResponse)
def sso_new_form(request: Request, state: ServerState = Depends(get_server_state)) -> HTMLResponse:
    provider = OidcProvider(state.app_ctx.oidc_providers)
    return templates.TemplateResponse(request, "admin/oidc_provider_form.html", _form_context(provider))


@router.post("/new", response_class=HTMLResponse, response_model=None)
def sso_new_submit(
    request: Request,
    provider_form: Annotated[OidcProviderForm, Form()],
    state: ServerState = Depends(get_server_state),
) -> HTMLResponse | RedirectResponse:
    provider = OidcProvider(state.app_ctx.oidc_providers)
    return _save_provider(request, provider, provider_form)


@router.get("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def sso_edit_form(
    uuid: str, request: Request, state: ServerState = Depends(get_server_state)
) -> HTMLResponse | RedirectResponse:
    provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
    if provider is None:
        return RedirectResponse(url="/admin/sso?error=Provider+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "admin/oidc_provider_form.html", _form_context(provider))


@router.post("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def sso_edit_submit(
    uuid: str,
    request: Request,
    provider_form: Annotated[OidcProviderForm, Form()],
    state: ServerState = Depends(get_server_state),
) -> HTMLResponse | RedirectResponse:
    provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
    if provider is None:
        return RedirectResponse(url="/admin/sso?error=Provider+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return _save_provider(request, provider, provider_form)


def _save_provider(
    request: Request, provider: OidcProvider, provider_form: OidcProviderForm
) -> HTMLResponse | RedirectResponse:
    provider_form.apply_to(provider)

    try:
        provider.save()
    except ValueError as e:
        context = _form_context(provider)
        context["error"] = str(e)
        return templates.TemplateResponse(request, "admin/oidc_provider_form.html", context)

    return RedirectResponse(url="/admin/sso?msg=Provider+saved", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------------------------------
@router.post("/{uuid}/delete")
def sso_delete(uuid: str, state: ServerState = Depends(get_server_state)) -> RedirectResponse:
    provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
    if provider is None:
        return RedirectResponse(url="/admin/sso?error=Provider+not+found", status_code=status.HTTP_303_SEE_OTHER)

    try:
        provider.delete()
    except LookupError as e:
        return RedirectResponse(url=f"/admin/sso?error={e}", status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(url="/admin/sso?msg=Provider+deleted", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete")
def sso_delete_bulk(
    uuids: list[str] = Form(default_factory=list), state: ServerState = Depends(get_server_state)
) -> RedirectResponse:
    nb_ok = 0
    nb_errors = 0
    for uuid in uuids:
        provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
        if provider is None:
            nb_errors += 1
            continue
        try:
            provider.delete()
            nb_ok += 1
        except LookupError:
            nb_errors += 1

    msg = f"{nb_ok}+provider(s)+deleted" + (f",+{nb_errors}+error(s)" if nb_errors else "")
    return RedirectResponse(url=f"/admin/sso?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)
