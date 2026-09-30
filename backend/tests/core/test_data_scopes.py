"""The data-scope vocabulary, and the two numbers it has to agree with.

``docs/bi.md`` §Phase 4 adds the last dimension the indivisible binding row was
missing: WHICH SLICE of an institution's book the sentence admits. The pure half
of that lives in ``app.core.authorization`` — the stored vocabulary, the value
normaliser, the machine-bundle set and the reduction — and is checked here
without a database, because every one of these is a policy statement rather than
a query.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import String

from app.core.authorization import (
    DATA_SCOPE_VALUE_MAX_LENGTH,
    MACHINE_ROLE_BUNDLES,
    ROLE_PERMISSIONS,
    BindingGrant,
    BindingStatus,
    DataScope,
    InstitutionScope,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
    normalise_data_scope_values,
    principal_bundle_compatible,
)
from app.models.bi import BiDimBranch
from app.services.authorization import (
    ALL_INSTITUTION_DATA,
    NO_INSTITUTION_DATA,
    AuthorizationInvariantError,
    BindingScope,
    EffectiveDataScope,
    data_scope_column_values,
    reduce_data_scope,
)

_NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def _grant(
    data_scope: DataScope,
    *values: str,
    binding_id: UUID | None = None,
) -> BindingGrant:
    return BindingGrant(
        binding_id=binding_id or uuid4(),
        organization_id="OR-DEM00001",
        principal_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id="BK-SAMP0001",
        module_scope=ModuleScope.ALL,
        sensitivity_scope=SensitivityScope.ALL,
        status=BindingStatus.ACTIVE,
        valid_from=_NOW,
        valid_until=None,
        revoked_at=None,
        data_scope=data_scope,
        data_scope_values=values,
    )


# --- the stored vocabulary ----------------------------------------------------


def test_the_stored_vocabulary_is_three_kinds_and_excludes_the_derived_two() -> None:
    """``mixed`` and ``none`` describe a union; no row may carry either."""
    assert [kind.value for kind in DataScope] == ["all", "branch", "region"]
    assert "mixed" not in {kind.value for kind in DataScope}
    assert "none" not in {kind.value for kind in DataScope}


def test_the_value_length_limit_matches_the_dimension_it_must_match_against() -> None:
    """A longer grant could never match a branch, so it is refused when written.

    The account plane may not import the BI plane, so the number is restated in
    ``core.authorization``; this test is the only place the two meet, and it is a
    test, where reaching into ``bi_*`` costs nothing.
    """
    columns = BiDimBranch.__table__.columns
    for name in ("branch_code", "region"):
        column_type = columns[name].type
        assert isinstance(column_type, String), f"{name} is no longer a bounded string"
        assert column_type.length == DATA_SCOPE_VALUE_MAX_LENGTH, name


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ((), ()),
        (("ACC-001",), ("ACC-001",)),
        ((" ACC-001 ",), ("ACC-001",)),
        (("ACC-001", "ACC-001"), ("ACC-001",)),
        (("TEM-002", "ACC-001"), ("ACC-001", "TEM-002")),
        (("ACC-001", "", "   "), ("ACC-001",)),
        # Case is PRESERVED: a branch code is the core system's own token and
        # folding it would collide two genuinely different branches.
        (("acc-001", "ACC-001"), ("ACC-001", "acc-001")),
    ],
)
def test_values_are_trimmed_de_duped_and_ordered(
    given: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    assert normalise_data_scope_values(given) == expected


def test_two_spellings_of_one_grant_normalise_to_one_row_shape() -> None:
    assert normalise_data_scope_values(("ACC-001", " TEM-002 ")) == normalise_data_scope_values(
        ("TEM-002", "ACC-001", "ACC-001")
    )


# --- the machine bundle set ---------------------------------------------------


def test_bi_reader_reads_and_does_nothing_else() -> None:
    assert ROLE_PERMISSIONS[RoleBundle.BI_READER] == frozenset({Permission.VIEW})
    assert Permission.INGEST not in ROLE_PERMISSIONS[RoleBundle.BI_READER]
    # Disjoint by construction: neither machine integration can acquire the
    # other's authority merely by being issued.
    assert not (
        ROLE_PERMISSIONS[RoleBundle.BI_READER] & ROLE_PERMISSIONS[RoleBundle.INTEGRATION_WRITER]
    )


def test_every_bundle_has_a_permission_set() -> None:
    """Without this, a new bundle KeyErrors inside the evaluator at runtime."""
    assert set(ROLE_PERMISSIONS) == set(RoleBundle)


@pytest.mark.parametrize("bundle", MACHINE_ROLE_BUNDLES)
def test_a_machine_bundle_fits_a_machine_and_never_a_human(bundle: RoleBundle) -> None:
    assert principal_bundle_compatible(PrincipalType.MACHINE, bundle)
    assert not principal_bundle_compatible(PrincipalType.HUMAN, bundle)


@pytest.mark.parametrize(
    "bundle", [bundle for bundle in RoleBundle if bundle not in MACHINE_ROLE_BUNDLES]
)
def test_a_human_bundle_fits_a_human_and_never_a_machine(bundle: RoleBundle) -> None:
    assert principal_bundle_compatible(PrincipalType.HUMAN, bundle)
    assert not principal_bundle_compatible(PrincipalType.MACHINE, bundle)


def test_the_machine_set_holds_both_bundles_and_nothing_human() -> None:
    assert MACHINE_ROLE_BUNDLES == (RoleBundle.INTEGRATION_WRITER, RoleBundle.BI_READER)
    assert RoleBundle.VIEWER not in MACHINE_ROLE_BUNDLES


# --- the reduction ------------------------------------------------------------


def test_no_grants_serves_nothing_rather_than_everything() -> None:
    scope = reduce_data_scope(())

    assert scope is NO_INSTITUTION_DATA
    assert scope.kind == "none"
    assert scope.serves_nothing
    assert not scope.whole_institution, (
        "a forgetful reader must fall into the SCOPED path, where an empty value "
        "set yields no rows, rather than the whole-institution one"
    )


def test_one_whole_institution_grant_is_the_whole_institution() -> None:
    assert reduce_data_scope([_grant(DataScope.ALL)]) == ALL_INSTITUTION_DATA


def test_the_widest_grant_wins_because_bindings_or() -> None:
    """Narrowing would revoke authority the Org Owner actually granted."""
    scope = reduce_data_scope([_grant(DataScope.ALL), _grant(DataScope.BRANCH, "ACC-001")])

    assert scope == ALL_INSTITUTION_DATA
    assert scope.branches == ()


def test_branch_grants_union_and_stay_sorted_and_de_duped() -> None:
    scope = reduce_data_scope(
        [
            _grant(DataScope.BRANCH, "TEM-002", "ACC-001"),
            _grant(DataScope.BRANCH, "ACC-001", "KUM-003"),
        ]
    )

    assert scope == EffectiveDataScope(
        kind="branch", branches=("ACC-001", "KUM-003", "TEM-002"), regions=()
    )


def test_a_branch_grant_beside_a_region_grant_is_mixed_and_keeps_both() -> None:
    scope = reduce_data_scope(
        [_grant(DataScope.BRANCH, "ACC-001"), _grant(DataScope.REGION, "Ashanti")]
    )

    assert scope.kind == "mixed"
    assert scope.branches == ("ACC-001",)
    assert scope.regions == ("Ashanti",)
    assert not scope.whole_institution
    assert not scope.serves_nothing


def test_region_grants_alone_are_a_region_scope() -> None:
    scope = reduce_data_scope(
        [_grant(DataScope.REGION, "Greater Accra"), _grant(DataScope.REGION, "Ashanti")]
    )

    assert scope == EffectiveDataScope(
        kind="region", branches=(), regions=("Ashanti", "Greater Accra")
    )


# --- the column shape a service write produces --------------------------------
#
# ``create_role_binding`` is reachable without the API schema — staff
# provisioning, baseline membership and integration-key issuance all build a
# ``BindingScope`` directly — so the service must refuse an unusable scope itself
# rather than leaning on the request validator or the database CHECK.


def _scope(data_scope: DataScope, *values: str) -> BindingScope:
    return BindingScope(
        InstitutionScope.INSTITUTION,
        "BK-SAMP0001",
        ModuleScope.CREDIT,
        SensitivityScope.CONFIDENTIAL,
        data_scope,
        values,
    )


def test_a_whole_institution_scope_writes_sql_null_not_an_empty_list() -> None:
    """``all`` is the only kind whose list is NULL, and NULL is not ``[]``."""
    assert data_scope_column_values(_scope(DataScope.ALL)) is None


def test_a_narrow_scope_writes_its_normalised_values() -> None:
    assert data_scope_column_values(_scope(DataScope.BRANCH, " TEM-002 ", "ACC-001")) == [
        "ACC-001",
        "TEM-002",
    ]


def test_a_whole_institution_scope_carrying_values_is_refused() -> None:
    with pytest.raises(AuthorizationInvariantError, match="must not name branches or regions"):
        data_scope_column_values(_scope(DataScope.ALL, "ACC-001"))


@pytest.mark.parametrize("kind", [DataScope.BRANCH, DataScope.REGION])
def test_a_narrow_scope_naming_nothing_is_refused(kind: DataScope) -> None:
    """Including a list of only blanks, which normalises to nothing."""
    with pytest.raises(AuthorizationInvariantError, match="at least one"):
        data_scope_column_values(_scope(kind))
    with pytest.raises(AuthorizationInvariantError, match="at least one"):
        data_scope_column_values(_scope(kind, "   ", ""))


def test_a_value_too_long_to_ever_match_a_branch_is_refused() -> None:
    """Storing it would be a grant that silently matches nothing."""
    with pytest.raises(AuthorizationInvariantError, match="cannot match any branch"):
        data_scope_column_values(_scope(DataScope.BRANCH, "B" * (DATA_SCOPE_VALUE_MAX_LENGTH + 1)))
    # The boundary itself is admitted: the limit is inclusive.
    assert data_scope_column_values(
        _scope(DataScope.BRANCH, "B" * DATA_SCOPE_VALUE_MAX_LENGTH)
    ) == ["B" * DATA_SCOPE_VALUE_MAX_LENGTH]
