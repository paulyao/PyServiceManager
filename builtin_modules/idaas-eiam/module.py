"""IDaaS EIAM Module - Alibaba Cloud IDaaS EIAM API wrapper

Provides ListUsers, ListApplications, UpdateUser, AuthorizeApplicationToUsers
via the official alibabacloud-eiam20211201 SDK (AK/SK authentication).

Usage in service code:
    result = modules["idaas-eiam"].list_users(display_name="zhangsan")
    result = modules["idaas-eiam"].list_applications(application_name="app")
    result = modules["idaas-eiam"].update_user(user_id="uid", display_name="new")
    result = modules["idaas-eiam"].authorize_application_to_users(app_id="aid", user_ids=["uid"])
"""

import json
import threading


class Module:
    name = "idaas-eiam"
    version = "1.0.0"
    description = "Alibaba Cloud IDaaS EIAM API module"

    def __init__(self):
        self._client = None
        self._instance_id = None
        self._timeout = 30
        self._lock = threading.Lock()

    def on_start(self, ctx):
        self._init_client(ctx.module_config)
        ctx.logger.info(
            f"Module {self.name} started - service: {ctx.service_name}, "
            f"instance_id={self._instance_id}"
        )

    def on_stop(self, ctx):
        self._client = None
        ctx.logger.info(f"Module {self.name} stopped")

    def on_config_reload(self, ctx):
        self._init_client(ctx.module_config)
        ctx.logger.info(f"Module {self.name} config reloaded")

    def _init_client(self, config):
        """Initialize or reinitialize the SDK client from config."""
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_eiam20211201.client import Client as EiamClient

        eiam_cfg = config.get("eiam", {})
        ak = eiam_cfg.get("access_key_id", "")
        sk = eiam_cfg.get("access_key_secret", "")
        region_id = eiam_cfg.get("region_id", "cn-hangzhou")
        self._instance_id = eiam_cfg.get("instance_id", "")
        self._timeout = eiam_cfg.get("timeout", 30)

        if not ak or not sk or not self._instance_id:
            return

        open_api_cfg = open_api_models.Config(
            access_key_id=ak,
            access_key_secret=sk,
            endpoint=f"eiam.{region_id}.aliyuncs.com",
        )
        open_api_cfg.read_timeout = self._timeout * 1000
        open_api_cfg.connect_timeout = self._timeout * 1000

        with self._lock:
            self._client = EiamClient(open_api_cfg)

    def _ensure_client(self):
        """Ensure client is initialized, raise if not."""
        if self._client is None:
            raise RuntimeError("IDaaS EIAM client not initialized - check config")
        if not self._instance_id:
            raise RuntimeError("IDaaS EIAM instance_id not configured")

    def list_users(self, *, display_name=None, email=None, phone_number=None,
                   page_size=100, page_number=1):
        """List EIAM users with optional filters.

        Args:
            display_name: Filter by display name prefix (DisplayNameStartsWith).
            email: Filter by exact email.
            phone_number: Filter by exact phone number.
            page_size: Page size (max 100).
            page_number: Page number (1-based).

        Returns:
            dict: {"success": bool, "data": {"users": [...], "total_count": int}, "error": str|None}
        """
        try:
            self._ensure_client()
            from alibabacloud_eiam20211201 import models as m

            request = m.ListUsersRequest(
                instance_id=self._instance_id,
                display_name_starts_with=display_name,
                email=email,
                phone_number=phone_number,
                page_size=page_size,
                page_number=page_number,
            )

            from alibabacloud_tea_util import models as util_models
            runtime = util_models.RuntimeOptions()

            response = self._client.list_users_with_options(request, runtime)
            body = response.body

            users = []
            for u in (body.users or []):
                users.append({
                    "user_id": u.user_id,
                    "username": u.username,
                    "display_name": u.display_name,
                    "email": u.email,
                    "phone_number": u.phone_number,
                    "status": u.status,
                })

            return {
                "success": True,
                "data": {"users": users, "total_count": body.total_count or 0},
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    def get_user(self, *, user_id):
        """Get an EIAM user's detailed information including custom fields.

        Args:
            user_id: The user ID (required).

        Returns:
            dict: {"success": bool, "data": {"user_id": str, "display_name": str, "custom_fields": [...]}, "error": str|None}
        """
        try:
            self._ensure_client()
            from alibabacloud_eiam20211201 import models as m

            request = m.GetUserRequest(
                instance_id=self._instance_id,
                user_id=user_id,
            )

            from alibabacloud_tea_util import models as util_models
            runtime = util_models.RuntimeOptions()

            response = self._client.get_user_with_options(request, runtime)
            user = response.body.user

            custom_fields = []
            for cf in (user.custom_fields or []):
                custom_fields.append({
                    "field_name": cf.field_name,
                    "field_value": cf.field_value,
                })

            return {
                "success": True,
                "data": {
                    "user_id": user.user_id,
                    "display_name": user.display_name,
                    "username": user.username,
                    "email": user.email,
                    "phone_number": user.phone_number,
                    "status": user.status,
                    "custom_fields": custom_fields,
                },
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    def list_applications(self, *, application_name=None, page_size=100, page_number=1):
        """List EIAM applications with optional filter.

        Args:
            application_name: Filter by application name (left fuzzy match).
            page_size: Page size (max 100).
            page_number: Page number (1-based).

        Returns:
            dict: {"success": bool, "data": {"applications": [...], "total_count": int}, "error": str|None}
        """
        try:
            self._ensure_client()
            from alibabacloud_eiam20211201 import models as m

            request = m.ListApplicationsRequest(
                instance_id=self._instance_id,
                application_name=application_name,
                page_size=page_size,
                page_number=page_number,
            )

            from alibabacloud_tea_util import models as util_models
            runtime = util_models.RuntimeOptions()

            response = self._client.list_applications_with_options(request, runtime)
            body = response.body

            apps = []
            for a in (body.applications or []):
                apps.append({
                    "application_id": a.application_id,
                    "application_name": a.application_name,
                    "description": a.description,
                    "status": a.status,
                })

            return {
                "success": True,
                "data": {"applications": apps, "total_count": body.total_count or 0},
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    def update_user(self, *, user_id, display_name=None, email=None,
                    phone_number=None, username=None, custom_fields=None):
        """Update an EIAM user's information.

        Args:
            user_id: The user ID to update (required).
            display_name: New display name.
            email: New email.
            phone_number: New phone number.
            username: New username.
            custom_fields: List of custom field dicts, e.g.
                [{"field_name": "department", "field_value": "IT", "operation": "SET"}]

        Returns:
            dict: {"success": bool, "data": {"request_id": str}, "error": str|None}
        """
        try:
            self._ensure_client()
            from alibabacloud_eiam20211201 import models as m

            # Build custom_fields list for SDK
            sdk_custom_fields = None
            if custom_fields:
                sdk_custom_fields = []
                for cf in custom_fields:
                    sdk_custom_fields.append(m.UpdateUserRequestCustomFields(
                        field_name=cf.get("field_name"),
                        field_value=cf.get("field_value"),
                        operation=cf.get("operation"),
                    ))

            request = m.UpdateUserRequest(
                instance_id=self._instance_id,
                user_id=user_id,
                display_name=display_name,
                email=email,
                phone_number=phone_number,
                username=username,
                custom_fields=sdk_custom_fields,
            )

            from alibabacloud_tea_util import models as util_models
            runtime = util_models.RuntimeOptions()

            response = self._client.update_user_with_options(request, runtime)

            return {
                "success": True,
                "data": {"request_id": response.body.request_id},
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    def authorize_application_to_users(self, *, app_id, user_ids):
        """Authorize an application to multiple users.

        Args:
            app_id: The application ID (required).
            user_ids: List of user IDs to authorize (required).

        Returns:
            dict: {"success": bool, "data": {"request_id": str}, "error": str|None}
        """
        try:
            self._ensure_client()
            from alibabacloud_eiam20211201 import models as m

            request = m.AuthorizeApplicationToUsersRequest(
                instance_id=self._instance_id,
                application_id=app_id,
                user_ids=user_ids,
            )

            from alibabacloud_tea_util import models as util_models
            runtime = util_models.RuntimeOptions()

            response = self._client.authorize_application_to_users_with_options(request, runtime)

            return {
                "success": True,
                "data": {"request_id": response.body.request_id},
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}
