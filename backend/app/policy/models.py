"""Tenant registry and policy definition -- stage one of the pipeline.

    **Policy Definition** -> Attribute Resolution -> Policy Decision -> Enforcement

Policies are *data*, owned by a tenant and filtered by the session guards like
any other tenant data. A condition names an attribute key, an operator and a
list of literal operands; it does not know what the attribute means. That is
what keeps the four undefined attribute domains out of the schema.

**Tenant no longer lives here.** It moved to :mod:`app.platform.models` in
P02. The tenant registry is control-plane data that Identity, Audit, Storage and
Events all reference, and holding it in Policy forced those tables to carry
``tenant_id`` as a bare UUID -- a foreign key would have inverted the context
map. With Tenant in a context below all of them, the foreign keys are legal and
are now enforced. This resolves assumption **A-18**; see ADR-0010.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import TenantScopedBase


class Policy(TenantScopedBase):
    """A rule granting or refusing an action on a resource type.

    ``effect`` is stored as text with the allowed values constrained in the
    application rather than as a PostgreSQL enum type: an enum would need a
    migration to add a value, and the set of effects is the kind of thing the
    requirements may yet extend.
    """

    __tablename__ = "policy"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_policy_tenant_id_name"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: "allow" or "deny" -- see app.policy.engine.Effect.
    effect: Mapped[str] = mapped_column(String(16), nullable=False)

    #: The action and resource type this policy speaks to. ``*`` matches any.
    action: Mapped[str] = mapped_column(String(128), nullable=False, default="*")
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False, default="*")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    conditions: Mapped[list[PolicyCondition]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PolicyCondition(TenantScopedBase):
    """One attribute test within a policy.

    All conditions on a policy must hold for it to apply. There is no OR
    between conditions and no nesting: expressing a disjunction means writing a
    second policy. That keeps evaluation total and obviously terminating, and
    avoids inventing a rule language before the requirements ask for one.
    """

    __tablename__ = "policy_condition"

    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("policy.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Namespaced key, e.g. ``subject.id``. Not constrained to a known set:
    #: a policy may reference an attribute no resolver provides yet, which
    #: evaluates as unresolved rather than as an error.
    attribute_key: Mapped[str] = mapped_column(String(128), nullable=False)

    #: A name from app.policy.operators. Validated at evaluation time, so a
    #: policy written against an operator added later does not break on read.
    operator: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Literal operands, as a JSON array of strings.
    operands: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    policy: Mapped[Policy] = relationship(back_populates="conditions", lazy="joined")
