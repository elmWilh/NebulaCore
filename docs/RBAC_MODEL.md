# RBAC And Security Model

## Big Picture

Nebula currently uses a layered authorization model rather than one single universal RBAC table.

There are three main layers:

1. session and actor type
2. identity role tagging
3. container-specific permissions

Understanding the project gets much easier once those layers are separated mentally.

## Layer 1: Session Identity

Authentication is based on a signed cookie called `nebula_session`.

The cookie payload stores:

- username
- database name
- expiration timestamp
- random nonce

The cookie is signed with `NEBULA_SESSION_SECRET` in `nebula_core/api/security.py`.

When Core receives a request, it resolves:

- `username`
- `db_name`
- `is_staff`

That tuple is the main session context used across the backend.

## Layer 2: Staff vs Non-Staff

Staff is the first major branch in authorization.

### Staff users

- live in `system.db`
- must have `is_staff = 1`
- can access administrative routes
- can create and modify roles
- can deploy containers
- can manage plugin runtime and project metadata

### Non-staff users

- may live in `system.db` or a client DB
- authenticate through `/users/login`
- can only work with resources explicitly assigned to them

## Layer 3: Identity Roles

Global role catalog:

- table: `identity_roles`
- API: `/roles/list`, `/roles/create`

Per-user identity tag:

- table: `user_identity_tags`
- API: `/users/identity-tag`

This system is used to answer the question:

> "What business role does this user have in this database context?"

Examples:

- `user`
- `moderator`
- `developer`
- `tester`
- `admin`

The role tag is global metadata in `system.db`, even when the user record itself lives in a client DB.

## Layer 4: Container Access Assignment

Being logged in is not enough to see a container.

For non-staff users, visibility is gated by `container_permissions` in `system.db`.

Each row links:

- `container_id`
- `username`
- `db_name`
- `role_tag`

Meaning:

- staff sees all containers
- non-staff sees only explicitly assigned containers

This is the access-entry layer.

## Layer 5: Container Capability Matrix

After a user has access to a container, Nebula computes what they are allowed to do inside it.

That comes from `container_role_permissions`.

Capabilities include:

- `allow_explorer`
- `allow_root_explorer`
- `allow_console`
- `allow_shell`
- `allow_settings`
- `allow_edit_files`
- `allow_edit_startup`
- `allow_edit_ports`

Default matrices are defined in `nebula_core/services/docker_service.py`.

The effective policy is:

1. resolve role tag for the user
2. load default role permissions
3. overlay container-specific role policy
4. if `is_staff`, force everything to `true`

## Practical Consequence

Two different users with the same `role_tag` can have:

- different visible containers, because assignments differ
- the same capabilities on those containers, because role policy is shared

This is a clean split between:

- access to the object
- rights inside the object

## Legacy / Parallel RBAC Tables

Client databases may still contain classic tables:

- `roles`
- `permissions`
- `user_roles`
- `role_permissions`

Those are still referenced by parts of the user service and legacy endpoints like `/roles/assign`.

Today, however, the most important live authorization path for the panel is:

- session context
- `identity_roles`
- `user_identity_tags`
- `container_permissions`
- `container_role_permissions`

So if you are documenting or extending the current product behavior, this layered model is the one to focus on first.

## Internal Token Access

Some routes can be authorized by internal automation instead of a staff session:

- header: `X-Nebula-Token`
- source secret: `NEBULA_INSTALLER_TOKEN`

This is used by:

- installer/bootstrap flow
- some plugin and system operations
- internal observability access

## 2FA

TOTP-based 2FA exists for both staff and regular users.

User endpoints:

- `GET /users/2fa/status`
- `POST /users/2fa/setup`
- `POST /users/2fa/confirm`
- `POST /users/2fa/disable`

Admin login also enforces TOTP when enabled on the account.

## Password Reset

Password reset uses short-lived email codes.

Flow:

1. `/users/password-reset/request`
2. code stored hashed in `system.db`
3. email delivery via configured mailer
4. `/users/password-reset/confirm`

The API intentionally returns generic responses to reduce account enumeration risk.

## Recommendations For Future Contributors

- Treat `identity_roles` and container role matrices as the canonical modern model.
- Treat client-DB classic RBAC tables as compatibility data unless your feature explicitly depends on them.
- Keep `system.db` as the global policy layer.
- When adding a new sensitive container action, always wire it through effective container permissions and audit logging.
