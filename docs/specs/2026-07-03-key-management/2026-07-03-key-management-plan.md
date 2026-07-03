# Key management Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **1 of 9** · Item: **Key management**

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers/subagent-driven-development` (recommended) or `superpowers/executing-plans` to implement this plan task-by-task. **Complete Step 0 below before any code change** — checkout the feature branch, then create `-revision.md`. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store LLM and service credentials in encrypted Postgres (`apps.keys`), resolve them at operation time, and expose a write-only Settings / Keys UI — env vars remain LLM fallback only.

**Architecture:** New leaf app `apps.keys` (Fernet-encrypted `SystemCredential` + `UserCredential`). Apps inject `secret_supplier` callables into `libs/providers` at the runner boundary (libs never import `apps.keys`). Resolution order: user default → system default → env (LLM only).

**Tech Stack:** Django 5.2, Fernet (`cryptography`), Pydantic, Jinja2 + htmx, existing `libs/providers`.

**Branch:** `feat/2026-07-03-key-management`

**Design spec:** [`2026-07-03-key-management-design.md`](./2026-07-03-key-management-design.md)
**Arch rules (brief):** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md)
**Superpowers policy:** [`olib/docs/specs/01-superpowers/01-superpowers.spec.md`](../../olib/docs/specs/01-superpowers/01-superpowers.spec.md)

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do **not** start S0 Task 1 (or write any application code) until every checkbox here is done. This matches **Step 0** in `superpowers/executing-plans` and `superpowers/subagent-driven-development`.

- [ ] **Step 0a: Checkout feature branch**

Read **Branch:** from this plan header (must match `-design.md`). From the `chief/` repo root:

```bash
git checkout feat/2026-07-03-key-management || git checkout -b feat/2026-07-03-key-management
git branch --show-current   # must print feat/2026-07-03-key-management
```

**Never** implement on `main`, `master`, or the repository default branch. If you are on the default branch, stop and switch before continuing.

Optional isolation: use `superpowers/using-git-worktrees` to work in `.worktrees/` on the same branch name.

- [ ] **Step 0b: Create review template**

Create `docs/specs/2026-07-03-key-management/2026-07-03-key-management-revision.md` from the **Revision template** in [`olib/docs/specs/01-superpowers/01-superpowers.spec.md`](../../olib/docs/specs/01-superpowers/01-superpowers.spec.md). Leave it empty for the human reviewer — **do not read it during implementation**.

- [ ] **Step 0c: Commit pre-implementation artifacts (if not already on branch)**

If Step 0 files were created on the feature branch and are uncommitted, commit and sync:

```bash
git add docs/specs/2026-07-03-key-management/2026-07-03-key-management-revision.md
git commit -m "docs(keys): add implementation review template"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

Skip 0c if `-revision.md` is already committed on the feature branch.

---

## Conventions

- Commands from repo root; prefix per `AGENTS.md`: `./olib/scripts/orunr …`
- Required gate after each stage: `./olib/scripts/orunr py test-all`
- Migrations: `./olib/scripts/orunr django manage makemigrations keys` (never hand-write)
- Test base: `olib.py.django.test.cases.OTransactionTestCase`
- Avoid parproc keywords in test names (`error`, `exception`, …)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts; never commit on `main`/`master`/default branch

---

## Target file map

```
backend/apps/keys/
  __init__.py
  apps.py
  models.py              # SystemCredential, UserCredential
  crypto.py              # encrypt / decrypt
  types.py               # SERVICE_TYPES, LLM_ENV_FALLBACK
  exceptions.py
  admin.py
  services/
    __init__.py
    queries.py           # KeyMetadata, list_*, resolve_*, make_secret_supplier
    commands.py          # set_user_default, upsert_user_named, delete_*, system cmds
  migrations/0001_initial.py
  tests/
    test_crypto.py
    test_commands.py
    test_queries.py

backend/apps/runner/
  llm_config.py          # inject secret_supplier + user_id
  loop.py                # pass user_id into provider_config_from_spec

backend/libs/providers/
  types.py               # + credential_ref, user_id, secret_supplier
  openai_provider.py     # resolve via supplier in get_client; no cached client across keys
  anthropic_provider.py
  local_openai_provider.py

backend/apps/sessions/
  tasks.py               # resolve before generate_chat_name

backend/apps/web/
  views.py               # keys page + POST handlers
  urls.py                # + /settings/keys/

backend/templates/web/
  keys.html
  base.html              # Keys nav link

chief/settings.py        # + apps.keys

.env.local.example       # + CHIEF_CREDENTIALS_KEY, LOCAL_OPENAI_API_KEY
```

---

## Locked decisions

| Topic | Decision |
|-------|----------|
| Libs boundary | Providers receive `secret_supplier: Callable[[], str \| None]` on `ProviderLLMConfig`, built in `apps/runner/llm_config.py` via `make_secret_supplier`. **No `apps.keys` imports in `libs/*`.** |
| Cached SDK client | Drop `_client` caching on OpenAI/Anthropic providers when using suppliers — build client per `stream()`/`collect()` call so key rotation takes effect. |
| `credential_ref` in YAML | **Deferred to spec 2.** v1 always uses default for provider type; `ProviderLLMConfig.credential_ref` wired but always `None` until spec 2 extends `LLMSpec`. |
| Chat name task | Task loads `session.agent.user_id`, builds `ProviderLLMConfig` with supplier, passes to extended `generate_chat_name`. |
| System admin | Django admin for `SystemCredential` in v1 (staff write-only). |
| Empty password on default slot | Clears user default (falls through to system → env). |

---

## S0 — Scaffold `apps.keys`

**Prerequisite:** Step 0 complete (feature branch checked out; `-revision.md` exists).

### Task 1: App package + models

**Files:**
- Create: `backend/apps/keys/__init__.py`, `apps.py`, `models.py`, `exceptions.py`
- Modify: `backend/chief/settings.py`
- Test: `backend/apps/keys/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/keys/tests/test_models.py
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from apps.keys.models import CredentialRole, SystemCredential, UserCredential
from olib.py.django.test.cases import OTransactionTestCase


class TestCredentialModels(OTransactionTestCase):
    def test_system_default_name_is_canonical(self) -> None:
        row = SystemCredential.objects.create(
            name='default:openai',
            role=CredentialRole.DEFAULT,
            type='openai',
            encrypted_value=b'ciphertext',
        )
        self.assertEqual(row.name, 'default:openai')

    def test_user_name_unique_per_user(self) -> None:
        user = get_user_model().objects.create_user(username='u1', password='x')
        UserCredential.objects.create(
            user=user,
            name='gmail-personal',
            role=CredentialRole.NAMED,
            type='gmail',
            encrypted_value=b'x',
        )
        with self.assertRaises(IntegrityError):
            UserCredential.objects.create(
                user=user,
                name='gmail-personal',
                role=CredentialRole.NAMED,
                type='gmail',
                encrypted_value=b'y',
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/keys/tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.keys'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/apps/keys/apps.py
from django.apps import AppConfig


class KeysConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.keys'
    label = 'keys'
```

```python
# backend/apps/keys/models.py
from django.conf import settings
from django.db import models
from django.db.models import Q

from olib.py.utils.uuid7 import uuid7


class CredentialRole(models.TextChoices):
    DEFAULT = 'default', 'Default'
    NAMED = 'named', 'Named'


class SystemCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    name = models.CharField(max_length=64)
    role = models.CharField(max_length=16, choices=CredentialRole.choices)
    type = models.CharField(max_length=32)
    encrypted_value = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['name'], name='keys_systemcredential_name_uniq'),
            models.UniqueConstraint(
                fields=['type'],
                condition=Q(role=CredentialRole.DEFAULT),
                name='keys_systemcredential_default_per_type_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['type']),
        ]


class UserCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='credentials')
    name = models.CharField(max_length=64)
    role = models.CharField(max_length=16, choices=CredentialRole.choices)
    type = models.CharField(max_length=32)
    encrypted_value = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='keys_usercredential_user_name_uniq'),
            models.UniqueConstraint(
                fields=['user', 'type'],
                condition=Q(role=CredentialRole.DEFAULT),
                name='keys_usercredential_default_per_type_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'name']),
            models.Index(fields=['user', 'type']),
        ]
```

Add `'apps.keys'` to `INSTALLED_APPS` in `backend/chief/settings.py`.

- [ ] **Step 4: Generate migration and run tests**

```bash
./olib/scripts/orunr django manage makemigrations keys
./olib/scripts/orunr django manage migrate
./olib/scripts/orunr py test backend/apps/keys/tests/test_models.py -v
```

Expected: PASS

- [ ] **Step 5: Commit (PR-ready chunk)**

```bash
git add backend/apps/keys backend/chief/settings.py
git commit -m "feat(keys): scaffold apps.keys models and migration"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

### Task 2: Crypto module

**Files:**
- Create: `backend/apps/keys/crypto.py`, `backend/apps/keys/tests/test_crypto.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/keys/tests/test_crypto.py
from cryptography.fernet import Fernet, InvalidToken

from apps.keys import crypto
from olib.py.django.test.cases import OTransactionTestCase


class TestCredentialCrypto(OTransactionTestCase):
    def test_round_trip(self) -> None:
        ciphertext = crypto.encrypt('sk-test-secret')
        self.assertEqual(crypto.decrypt(ciphertext), 'sk-test-secret')

    def test_wrong_key_raises(self) -> None:
        ciphertext = crypto.encrypt('sk-test-secret')
        other = Fernet(Fernet.generate_key())
        with self.assertRaises(InvalidToken):
            other.decrypt(ciphertext)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/keys/tests/test_crypto.py -v`
Expected: FAIL — `cannot import name 'encrypt'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/apps/keys/crypto.py
from __future__ import annotations

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _master_key_bytes() -> bytes:
    explicit = getattr(settings, 'CHIEF_CREDENTIALS_KEY', None) or ''
    if explicit:
        return explicit.encode('ascii')
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'chief-credentials-v1',
        info=b'fernet-key',
    ).derive(settings.SECRET_KEY.encode('utf-8'))
    return base64.urlsafe_b64encode(derived)


def _fernet() -> Fernet:
    try:
        return Fernet(_master_key_bytes())
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured('credential storage misconfigured') from exc


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode('utf-8'))


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode('utf-8')
```

Add to `backend/chief/settings.py` (read from env):

```python
CHIEF_CREDENTIALS_KEY = env.str('CHIEF_CREDENTIALS_KEY', default='')  # noqa: F405
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./olib/scripts/orunr py test backend/apps/keys/tests/test_crypto.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/keys/crypto.py backend/apps/keys/tests/test_crypto.py backend/chief/settings.py
git commit -m "feat(keys): add Fernet encrypt/decrypt with dev HKDF fallback"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

### Task 3: Type registry

**Files:**
- Create: `backend/apps/keys/types.py`, `backend/apps/keys/tests/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/keys/tests/test_types.py
from apps.keys.exceptions import KeyValidationError
from apps.keys.types import LLM_ENV_FALLBACK, is_registered_type, validate_type
from olib.py.django.test.cases import OTransactionTestCase


class TestServiceTypes(OTransactionTestCase):
    def test_openai_is_registered(self) -> None:
        self.assertTrue(is_registered_type('openai'))

    def test_unknown_type_rejected(self) -> None:
        with self.assertRaises(KeyValidationError):
            validate_type('not-a-service')

    def test_llm_env_fallback_map(self) -> None:
        self.assertEqual(LLM_ENV_FALLBACK['openai'], 'OPENAI_API_KEY')
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement**

```python
# backend/apps/keys/exceptions.py
class KeyNotFoundError(LookupError):
    pass


class KeyValidationError(ValueError):
    pass


class KeyTypeMismatchError(ValueError):
    pass
```

```python
# backend/apps/keys/types.py
from __future__ import annotations

import re

from apps.keys.exceptions import KeyValidationError

SERVICE_TYPES: frozenset[str] = frozenset({
    'openai', 'anthropic', 'local_openai', 'gmail', 'clickup', 'obsidian',
})

LLM_ENV_FALLBACK: dict[str, str] = {
    'openai': 'OPENAI_API_KEY',
    'anthropic': 'ANTHROPIC_API_KEY',
    'local_openai': 'LOCAL_OPENAI_API_KEY',
}

USER_NAMED_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
RESERVED_USER_PREFIXES = ('default:', 'sys:')


def is_registered_type(type_name: str) -> bool:
    return type_name in SERVICE_TYPES


def validate_type(type_name: str) -> str:
    if not is_registered_type(type_name):
        raise KeyValidationError(f'unknown credential type: {type_name}')
    return type_name


def canonical_default_name(type_name: str) -> str:
    validate_type(type_name)
    return f'default:{type_name}'
```

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(keys): add service type registry and validation"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## S1 — Services (queries + commands)

### Task 4: Commands

**Files:**
- Create: `backend/apps/keys/services/commands.py`, `backend/apps/keys/tests/test_commands.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/keys/tests/test_commands.py
from django.contrib.auth import get_user_model

from apps.keys.exceptions import KeyNotFoundError, KeyValidationError
from apps.keys.models import SystemCredential, UserCredential
from apps.keys.services import commands
from olib.py.django.test.cases import OTransactionTestCase


class TestCredentialCommands(OTransactionTestCase):
    def test_set_user_default_encrypts_and_lists_metadata(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user', password='x')
        meta = commands.set_user_default(user.pk, 'openai', 'sk-user-key')
        self.assertTrue(meta.is_set)
        self.assertEqual(meta.name, 'default:openai')
        self.assertEqual(meta.scope, 'user')
        row = UserCredential.objects.get(user_id=user.pk, name='default:openai')
        self.assertNotEqual(row.encrypted_value, b'sk-user-key')

    def test_upsert_named_rejects_reserved_prefix(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user2', password='x')
        with self.assertRaises(KeyValidationError):
            commands.upsert_user_named(user.pk, 'default:evil', 'gmail', 'token')

    def test_delete_user_credential_idempotent_missing(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user3', password='x')
        with self.assertRaises(KeyNotFoundError):
            commands.delete_user_credential(user.pk, 'missing')

    def test_user_name_cannot_collide_with_system_namespace(self) -> None:
        SystemCredential.objects.create(
            name='shared-name',
            role='named',
            type='clickup',
            encrypted_value=b'x',
        )
        user = get_user_model().objects.create_user(username='cmd-user4', password='x')
        with self.assertRaises(KeyValidationError):
            commands.upsert_user_named(user.pk, 'shared-name', 'clickup', 'tok')
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement commands** (full module per design spec API):

Key functions: `set_user_default`, `upsert_user_named`, `delete_user_credential`,
`set_system_default`, `delete_system_credential`. Each validates type, name regex,
reserved prefixes, system namespace collision, secret length ≤ 16 KiB. Empty secret
on `set_user_default` deletes the row. Return `KeyMetadata` dataclass (no plaintext).

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(keys): add credential write commands"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

### Task 5: Metadata + resolve queries

**Files:**
- Create: `backend/apps/keys/services/queries.py`, `backend/apps/keys/tests/test_queries.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/keys/tests/test_queries.py
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model

from apps.keys.exceptions import KeyNotFoundError, KeyTypeMismatchError
from apps.keys.services import commands, queries
from olib.py.django.test.cases import OTransactionTestCase


class TestCredentialQueries(OTransactionTestCase):
    def test_resolve_default_user_over_system_over_env(self) -> None:
        user = get_user_model().objects.create_user(username='q-user', password='x')
        commands.set_system_default('openai', 'sk-system')
        commands.set_user_default(user.pk, 'openai', 'sk-user')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env'}, clear=False):
            self.assertEqual(queries.resolve_default_secret(user.pk, 'openai'), 'sk-user')

    def test_resolve_default_falls_back_to_system_then_env(self) -> None:
        user = get_user_model().objects.create_user(username='q-user2', password='x')
        commands.set_system_default('openai', 'sk-system')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env'}, clear=False):
            self.assertEqual(queries.resolve_default_secret(user.pk, 'openai'), 'sk-system')

    def test_resolve_secret_by_system_name(self) -> None:
        user = get_user_model().objects.create_user(username='q-user3', password='x')
        commands.set_system_default('openai', 'sk-system')
        self.assertEqual(
            queries.resolve_secret(user.pk, 'default:openai', expected_type='openai'),
            'sk-system',
        )

    def test_type_mismatch_raises(self) -> None:
        user = get_user_model().objects.create_user(username='q-user4', password='x')
        commands.upsert_user_named(user.pk, 'my-clickup', 'clickup', 'tok')
        with self.assertRaises(KeyTypeMismatchError):
            queries.resolve_secret(user.pk, 'my-clickup', expected_type='gmail')

    def test_list_metadata_never_includes_plaintext(self) -> None:
        user = get_user_model().objects.create_user(username='q-user5', password='x')
        commands.set_user_default(user.pk, 'openai', 'sk-hidden')
        metas = queries.list_user_credentials(user.pk)
        payload = str(metas)
        self.assertNotIn('sk-hidden', payload)

    def test_no_cross_user_leakage(self) -> None:
        u1 = get_user_model().objects.create_user(username='q-u1', password='x')
        u2 = get_user_model().objects.create_user(username='q-u2', password='x')
        commands.upsert_user_named(u1.pk, 'private', 'gmail', 'tok1')
        with self.assertRaises(KeyNotFoundError):
            queries.resolve_secret(u2.pk, 'private', expected_type='gmail')
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement queries**

```python
# backend/apps/keys/services/queries.py — public API sketch
@dataclass(frozen=True)
class KeyMetadata:
    name: str
    scope: Literal['system', 'user']
    role: str
    type: str
    is_set: bool
    updated_at: datetime

def list_system_credentials() -> list[KeyMetadata]: ...
def list_user_credentials(user_id: int) -> list[KeyMetadata]: ...
def list_referenceable_credentials(user_id: int, *, type: str | None = None) -> list[KeyMetadata]: ...
def resolve_default_secret(user_id: int, type: str) -> str | None: ...
def resolve_secret(user_id: int, name: str, *, expected_type: str) -> str: ...
def make_secret_supplier(user_id: int, *, name: str | None = None, type: str) -> Callable[[], str]: ...
def get_llm_default_secret(user_id: int, provider: str) -> str | None: ...
```

`make_secret_supplier`: when `name` is `None`, closure calls `resolve_default_secret`;
else `resolve_secret`. Raises on missing/type mismatch **when called**, not when
constructed.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(keys): add metadata and resolve queries"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

### Task 6: Django admin (system credentials)

**Files:**
- Create: `backend/apps/keys/admin.py`

- [ ] **Step 1–4:** Register `SystemCredential` with write-only secret field on add/change;
list/detail show `is_set` only (custom `ModelAdmin` — no decrypt in views).

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(keys): add staff-only system credential admin"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

**S1 verify:** `./olib/scripts/orunr py test-all` green.

---

## S2 — Provider wiring

### Task 7: Extend `ProviderLLMConfig` + runner injection

**Files:**
- Modify: `backend/libs/providers/types.py`
- Modify: `backend/apps/runner/llm_config.py`
- Modify: `backend/apps/runner/loop.py`
- Test: `backend/apps/runner/tests/test_llm_config.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/runner/tests/test_llm_config.py
from unittest.mock import MagicMock

from apps.agents.spec import LLMSpec
from apps.runner.llm_config import provider_config_from_spec
from olib.py.django.test.cases import OTransactionTestCase


class TestProviderConfigFromSpec(OTransactionTestCase):
    def test_includes_user_id_and_secret_supplier(self) -> None:
        llm = LLMSpec(provider='openai', model='gpt-5.4-mini')
        cfg = provider_config_from_spec(llm, user_id=42)
        self.assertEqual(cfg.user_id, 42)
        self.assertIsNotNone(cfg.secret_supplier)
        supplier = MagicMock(return_value='sk-from-store')
        cfg.secret_supplier = supplier
        self.assertEqual(cfg.secret_supplier(), 'sk-from-store')
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement**

```python
# backend/libs/providers/types.py
from collections.abc import Callable
from pydantic import BaseModel, ConfigDict

class ProviderLLMConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    provider: str
    model: str
    temperature: float | None = None
    credential_ref: str | None = None
    user_id: int | None = None
    secret_supplier: Callable[[], str | None] | None = None
```

```python
# backend/apps/runner/llm_config.py
from apps.agents.spec import LLMSpec
from apps.keys.services.queries import make_secret_supplier
from libs.providers.types import ProviderLLMConfig


def provider_config_from_spec(
    llm: LLMSpec,
    *,
    user_id: int,
    credential_ref: str | None = None,
) -> ProviderLLMConfig:
    return ProviderLLMConfig(
        provider=llm.provider,
        model=llm.model,
        temperature=llm.temperature,
        credential_ref=credential_ref,
        user_id=user_id,
        secret_supplier=make_secret_supplier(
            user_id,
            name=credential_ref,
            type=llm.provider,
        ),
    )
```

```python
# backend/apps/runner/loop.py — in run(), where make_provider is called:
user_id = self.backend.session.agent.user_id
provider = make_provider(
    provider_config_from_spec(self.config_spec.llm, user_id=user_id),
)
```

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

### Task 8: OpenAI + Anthropic + LocalOpenAI providers use supplier

**Files:**
- Modify: `backend/libs/providers/openai_provider.py`, `anthropic_provider.py`, `local_openai_provider.py`
- Modify: `backend/libs/providers/tests/test_openai_provider.py` (add supplier test)

- [ ] **Step 1: Write the failing test**

```python
def test_stream_uses_secret_supplier_over_env(self) -> None:
    provider = OpenAIProvider('gpt-5.4-mini', secret_supplier=lambda: 'sk-supplied')
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-env'}, clear=False):
        with patch.object(provider, '_create_client') as mock_create:
            mock_create.return_value.chat.completions.create.return_value = iter([])
            list(provider.stream([], []))
            mock_create.assert_called_once()
            self.assertEqual(mock_create.call_args.kwargs['api_key'], 'sk-supplied')
```

Refactor `get_client()` → `_create_client(api_key)` called per request; remove
`self._client` cache. `_from_spec` passes `secret_supplier=llm.secret_supplier`.

Env fallback when supplier returns `None` or is absent.

- [ ] **Step 2–4: Implement + verify**

- [ ] **Step 5: Commit**

### Task 9: Chat name task resolves credentials

**Files:**
- Modify: `backend/libs/algorithms/chat_name.py`
- Modify: `backend/apps/sessions/tasks.py`
- Modify: `backend/apps/sessions/tests/test_tasks.py`

- [ ] **Step 1: Extend `generate_chat_name` to accept optional `llm: ProviderLLMConfig | None`**

When `llm` is provided, use it (with injected supplier). When `None`, keep
current behavior (env-only path for backward compat in direct lib calls).

- [ ] **Step 2: Update `generate_session_name` task**

```python
from apps.runner.llm_config import provider_config_from_spec
from apps.agents.spec import LLMSpec

# inside task, after loading session:
session = AgentSession.objects.select_related('agent').get(pk=uid)
user_id = session.agent.user_id
llm_cfg = provider_config_from_spec(
    LLMSpec(provider=DEFAULT_CHAT_NAME_CONFIG.provider, model=DEFAULT_CHAT_NAME_CONFIG.model,
            temperature=DEFAULT_CHAT_NAME_CONFIG.temperature),
    user_id=user_id,
)
name = generate_chat_name(text, config=DEFAULT_CHAT_NAME_CONFIG, llm=llm_cfg)
```

- [ ] **Step 3: Add test** with `set_user_default` + assert provider path used (mock collect).

- [ ] **Step 4: Commit**

**S2 verify:** `./olib/scripts/orunr py test-all` green; grep confirms no
`apps.keys` imports under `backend/libs/`.

---

## S3 — Settings UI

### Task 10: Keys page (GET)

**Files:**
- Create: `backend/templates/web/keys.html`
- Modify: `backend/apps/web/views.py`, `urls.py`, `base.html`
- Create: `backend/apps/web/tests/test_keys_page.py`

- [ ] **Step 1: Write the failing test**

```python
class TestKeysPage(OTransactionTestCase):
    def test_requires_login(self) -> None:
        response = self.client.get('/settings/keys/')
        self.assertEqual(response.status_code, 302)

    def test_shows_set_not_set_without_secret_material(self) -> None:
        user = get_user_model().objects.create_user(username='keys-user', password='x')
        self.client.login(username='keys-user', password='x')
        commands.set_user_default(user.pk, 'openai', 'sk-hidden')
        response = self.client.get('/settings/keys/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Set', response.content)
        self.assertNotIn(b'sk-hidden', response.content)
        self.assertNotIn(b'value="', response.content)  # no prefilled passwords
```

- [ ] **Step 2–4: Implement view** — `@login_required`, calls `list_user_credentials`
and metadata for LLM defaults; template with three sections per design spec;
password inputs `autocomplete="new-password"`, always empty.

- [ ] **Step 5: Add nav link in `base.html`**

```html
{% if user.is_authenticated %}
  <a href="{{ url('settings_keys') }}" style="color: #9ec5ff;">Keys</a> ·
{% endif %}
```

- [ ] **Step 6: Commit**

### Task 11: Keys page (POST handlers)

**Files:**
- Modify: `backend/apps/web/views.py`
- Extend: `backend/apps/web/tests/test_keys_page.py`

- [ ] **Step 1: Tests for POST set/clear/delete** — user scoped to `request.user.pk`;
validation failures return 400 with message; success redirects or htmx partial.

- [ ] **Step 2–4: Implement POST routes** (can be separate paths or single form with
`action` field):
- `set_user_default` per LLM provider
- service defaults
- `upsert_user_named` / `delete_user_credential`

Views import **commands + list queries only** — never `resolve_*`.

- [ ] **Step 5: Commit**

**S3 verify:** `./olib/scripts/orunr py test-all` green.

---

## S4 — Docs & env example

### Task 12: Environment + agent docs

**Files:**
- Modify: `.env.local.example`
- Modify: `AGENTS.local.md` (trim duplicate tables; point at `docs/ARCHITECTURE.md`)

- [ ] **Add to `.env.local.example` under `#[backend]`:**

```
CHIEF_CREDENTIALS_KEY=
LOCAL_OPENAI_API_KEY=
```

Document: generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

- [ ] **Commit**

**S4 verify:** `./olib/scripts/orunr py test-all` green.

---

## Manual verification

- [ ] Log in → **Keys** in header → set OpenAI key → shows **Set** on reload; password field empty.
- [ ] Start agent session → LLM call succeeds using stored key (remove `OPENAI_API_KEY` from env to confirm).
- [ ] Staff user → Django admin → system `default:openai` → non-staff sessions use it when user has no override.
- [ ] Clear user default → falls back to system then env.

---

## Spec coverage (self-review)

| Spec section | Task |
|--------------|------|
| Feature branch + review template (Superpowers Step 0) | Step 0 (before S0) |
| `apps.keys` models + encryption | S0 Tasks 1–3 |
| Services API (metadata + resolve + commands) | S1 Tasks 4–5 |
| System admin | S1 Task 6 |
| Provider resolve at call time | S2 Tasks 7–8 |
| `generate_session_name` | S2 Task 9 |
| Settings UI write-only | S3 Tasks 10–11 |
| Env fallback + `CHIEF_CREDENTIALS_KEY` | S0 Task 2, S4 Task 12 |
| Import boundaries | Locked decision + S2 verify grep |
| `credential_ref` in YAML | Deferred spec 2; field wired in types |
| Tool `key_ref` / `make_secret_supplier` for libs | S1 Task 5 delivers API; wiring in spec 2 |

---

## Downstream contract (unchanged — for spec 2+)

- `resolve_secret`, `make_secret_supplier`, `list_referenceable_credentials` are the
  integration surface.
- Tool factories pass `make_secret_supplier(user_id, name=key_ref, type=...)` into libs.
- Agent YAML gains `credential_ref` in spec 2; v1 uses defaults only.
