"""
Admin webui routes for managing OIDC providers - list, create, edit, delete.
Gated by require_admin_session (see admin_router.py)
"""

import logging
from typing import Annotated
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from Pytre_Core.oidc_providers import OidcProvider
from Pytre_Core.users import Users
from Pytre_Core.utils import log as _log
from Pytre_Server.routes.admin_auth import require_admin_session
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/sso")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


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


def _form_context(provider: OidcProvider, users: Users, saved_issuer: str | None = None) -> dict:
    """`saved_issuer`: the issuer stored in the database, when the form is redisplayed after a failed save -
    `provider` then already holds the submitted, unsaved one"""
    issuer = provider.issuer if saved_issuer is None else saved_issuer
    linked_users = users.find_users_by_oidc_issuer(issuer) if issuer else []
    return {
        "provider": provider,
        "saved_issuer": issuer,
        "linked_users": sorted(user.username for user in linked_users),
    }


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
    users = state.app_ctx.users
    linked_counts = {p.uuid: len(users.find_users_by_oidc_issuer(p.issuer)) for p in providers.values()}
    context = {"providers": providers, "linked_counts": linked_counts, "msg": msg, "error": error}
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
    context = _form_context(provider, state.app_ctx.users)
    return templates.TemplateResponse(request, "admin/oidc_provider_form.html", context)


@router.post("/new", response_class=HTMLResponse, response_model=None)
def sso_new_submit(
    request: Request,
    provider_form: Annotated[OidcProviderForm, Form()],
    state: ServerState = Depends(get_server_state),
    admin_username: str = Depends(require_admin_session),
) -> HTMLResponse | RedirectResponse:
    provider = OidcProvider(state.app_ctx.oidc_providers)
    return _save_provider(request, provider, provider_form, state.app_ctx.users, admin_username)


@router.get("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def sso_edit_form(
    uuid: str, request: Request, state: ServerState = Depends(get_server_state)
) -> HTMLResponse | RedirectResponse:
    provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
    if provider is None:
        return RedirectResponse(url="/admin/sso?error=Provider+not+found", status_code=status.HTTP_303_SEE_OTHER)
    context = _form_context(provider, state.app_ctx.users)
    return templates.TemplateResponse(request, "admin/oidc_provider_form.html", context)


@router.post("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def sso_edit_submit(
    uuid: str,
    request: Request,
    provider_form: Annotated[OidcProviderForm, Form()],
    state: ServerState = Depends(get_server_state),
    admin_username: str = Depends(require_admin_session),
) -> HTMLResponse | RedirectResponse:
    provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
    if provider is None:
        return RedirectResponse(url="/admin/sso?error=Provider+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return _save_provider(request, provider, provider_form, state.app_ctx.users, admin_username)


def _save_provider(
    request: Request, provider: OidcProvider, provider_form: OidcProviderForm, users: Users, admin_username: str
) -> HTMLResponse | RedirectResponse:
    saved_issuer = provider.issuer  # empty for a new provider
    provider_form.apply_to(provider)

    try:
        provider.save()
    except ValueError as e:
        context = _form_context(provider, users, saved_issuer=saved_issuer)
        context["error"] = str(e)
        return templates.TemplateResponse(request, "admin/oidc_provider_form.html", context)

    msg = f"OIDC provider '{provider.issuer}' saved by admin '{admin_username}' (enabled={provider.enabled})"
    if saved_issuer and saved_issuer != provider.issuer:
        nb_moved = users.replace_oidc_issuer(saved_issuer, provider.issuer)
        msg += f", {nb_moved} user link(s) moved from '{saved_issuer}'"

    log(msg, logging.WARNING)

    return RedirectResponse(url="/admin/sso?msg=Provider+saved", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------------------------------
@router.post("/{uuid}/delete")
def sso_delete(
    uuid: str, state: ServerState = Depends(get_server_state), admin_username: str = Depends(require_admin_session)
) -> RedirectResponse:
    provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
    if provider is None:
        return RedirectResponse(url="/admin/sso?error=Provider+not+found", status_code=status.HTTP_303_SEE_OTHER)

    try:
        nb_unlinked = state.app_ctx.users.replace_oidc_issuer(provider.issuer, None)
        provider.delete()
    except LookupError as e:
        return RedirectResponse(url=f"/admin/sso?error={e}", status_code=status.HTTP_303_SEE_OTHER)

    msg = f"OIDC provider '{provider.issuer}' deleted by admin '{admin_username}', {nb_unlinked} user link(s) removed"
    log(msg, logging.WARNING)
    return RedirectResponse(url="/admin/sso?msg=Provider+deleted", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete")
def sso_delete_bulk(
    uuids: list[str] = Form(default_factory=list),
    state: ServerState = Depends(get_server_state),
    admin_username: str = Depends(require_admin_session),
) -> RedirectResponse:
    nb_ok = 0
    nb_errors = 0
    nb_unlinked = 0
    for uuid in uuids:
        provider = state.app_ctx.oidc_providers.find_provider_by_uuid(uuid)
        if provider is None:
            nb_errors += 1
            continue
        try:
            nb_links = state.app_ctx.users.replace_oidc_issuer(provider.issuer, None)
            provider.delete()

            msg = f"OIDC provider '{provider.issuer}' deleted by admin '{admin_username}' (bulk), "
            msg += f"{nb_links} user link(s) removed"
            log(msg, logging.WARNING)

            nb_unlinked += nb_links
            nb_ok += 1
        except LookupError:
            nb_errors += 1

    msg = f"{nb_ok}+provider(s)+deleted" + (f",+{nb_errors}+error(s)" if nb_errors else "")
    msg += f",+{nb_unlinked}+user+link(s)+removed" if nb_unlinked else ""
    return RedirectResponse(url=f"/admin/sso?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)
