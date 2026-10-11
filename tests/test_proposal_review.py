"""Offline decision contracts; fixtures imported as modules, not test classes."""
import unittest
from pydantic import ValidationError
from tests import test_action_proposals as fixtures
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.proposal_review import (
    OfflineActorClaim, DecisionEvent, assess_proposal, fold_decisions,
)


class ProposalReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ActionProposalTests()
        fixture.setUp()
        self.proposal = ActionProposal(**fixture.data)
        self.capture, self.evidence = fixture.capture, [fixture.evidence]
        self.digest = "b" * 64  # Stand-in for the adapter-verified stored submission hash.
        self.actor = OfflineActorClaim(actor_id="explicit-human")

    def event(self, decision="approve_offline", version=0, **changes):
        data = dict(event_id=f"event-{version}", project_id="project", proposal_id="p1",
                    capture_id=self.capture.capture_id, proposal_body_sha256=self.digest,
                    expected_version=version, actor=self.actor, decision=decision)
        return DecisionEvent(**(data | changes))

    def fold(self, *events):
        return fold_decisions(self.proposal, self.capture, self.evidence, self.digest, events)

    def test_initial_and_explicit_grants(self):
        initial = self.fold()
        self.assertTrue(assess_proposal(self.proposal, self.capture, self.evidence).eligible)
        self.assertEqual((initial.review_status, initial.authorization_status,
                          initial.execution_status, initial.version),
                         ("pending", "not_authorized", "not_executed", 0))
        approved = self.fold(self.event())
        self.assertEqual(approved.review_status, "approved_offline")
        self.assertEqual(approved.authorization_status, "not_authorized")
        granted = self.fold(self.event(), self.event("authorize_dry_run", 1))
        self.assertEqual(granted.authorization_status, "authorized_dry_run")
        self.assertEqual(granted.permission_scope, "bound_proposal_dry_run")
        for projection in (initial, approved, granted):
            self.assertFalse(projection.real_execution_allowed)
            self.assertEqual(projection.execution_status, "not_executed")
        self.assertEqual(self.proposal.review_status, "pending")
        self.assertEqual(self.proposal.authorization_status, "not_authorized")

    def test_missing_or_invalid_technical_evidence(self):
        for change in ("missing", "unobserved", "wrong_fact"):
            with self.subTest(change=change):
                self.setUp()
                if change == "missing":
                    self.evidence = []
                elif change == "unobserved":
                    self.capture.snapshot.tasks[0].observed_fields = []
                else:
                    self.evidence[0].provenance.payload["priority"] = "low"
                self.assertFalse(assess_proposal(self.proposal, self.capture, self.evidence).eligible)
                with self.assertRaisesRegex(ValueError, "technical_ineligible"):
                    self.fold(self.event())
                self.assertEqual(self.fold(self.event("deny")).authorization_status, "denied")

    def test_transition_policy_and_withdrawal(self):
        with self.assertRaisesRegex(ValueError, "review_not_approved"):
            self.fold(self.event("authorize_dry_run"))
        approve, grant = self.event(), self.event("authorize_dry_run", 1)
        with self.assertRaisesRegex(ValueError, "review_not_pending"):
            self.fold(approve, self.event("approve_offline", 1))
        with self.assertRaisesRegex(ValueError, "review_not_approved"):
            self.fold(self.event("deny"), self.event("authorize_dry_run", 1))
        for decision, status in (("deny", "denied"), ("revoke", "revoked")):
            stop = self.event(decision, 2)
            projection = self.fold(approve, grant, stop)
            self.assertEqual(projection.authorization_status, status)
            renewed = self.fold(approve, grant, stop, self.event("authorize_dry_run", 3))
            self.assertEqual(renewed.authorization_status, "authorized_dry_run")
        rejected = self.fold(approve, grant, self.event("reject", 2))
        self.assertEqual((rejected.review_status, rejected.authorization_status),
                         ("rejected", "revoked"))
        for decision in ("approve_offline", "authorize_dry_run"):
            with self.assertRaisesRegex(ValueError, "review_terminal"):
                self.fold(self.event("reject"), self.event(decision, 1))
        for decision in ("deny", "revoke", "reject"):
            self.assertFalse(self.fold(self.event(decision)).real_execution_allowed)

    def test_replay_conflict_stale_and_binding(self):
        event = self.event()
        self.assertEqual(self.fold(event, event), self.fold(event))
        grant = self.event("authorize_dry_run", 1)
        self.assertEqual(self.fold(event, grant, event), self.fold(event, grant))
        with self.assertRaisesRegex(ValueError, "event_id_conflict"):
            self.fold(event, self.event("reject"))
        with self.assertRaisesRegex(ValueError, "stale_version"):
            self.fold(event, self.event("deny", event_id="new"))
        for changes in ({"project_id": "other"}, {"proposal_id": "other"},
                        {"capture_id": "other"}, {"proposal_body_sha256": "c" * 64}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "binding_mismatch"):
                self.fold(self.event(**changes))
        self.assertEqual(self.fold().version, 0)

    def test_strict_claims_and_events(self):
        for data in ({}, {"actor_id": " "}, {"actor_id": 1},
                     {"actor_id": "x", "provenance": "authenticated"}):
            with self.assertRaises(ValidationError):
                OfflineActorClaim(**data)
        for flag in ("authenticated", "admin", "role", "uid", "eligible"):
            with self.assertRaises(ValidationError):
                OfflineActorClaim(actor_id="x", **{flag: True})
        for changes in ({"expected_version": True}, {"expected_version": -1},
                        {"expected_version": "0"}, {"event_id": " "},
                        {"proposal_body_sha256": "B" * 64}, {"proposal_body_sha256": "abc"},
                        {"decision": "execute"}, {"live": True}, {"actor": None},
                        {"permission_scope": "live"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.event(**changes)
        with self.assertRaises(ValidationError):
            self.actor.actor_id = "changed"
        with self.assertRaises(ValidationError):
            self.event().decision = "reject"

    def test_forged_and_mutated_models_fail_closed(self):
        for actor in (self.actor.model_copy(update={"authenticated": True}),
                      OfflineActorClaim.model_construct(actor_id="x", provenance="admin")):
            with self.assertRaises(ValueError):
                self.fold(self.event().model_copy(update={"actor": actor}))
        for changes in ({"live": True}, {"expected_version": True},
                        {"decision": "execute"}):
            with self.assertRaises(ValueError):
                self.fold(self.event().model_copy(update=changes))
        self.proposal = self.proposal.model_copy(update={"authorization_status": "authorized"})
        self.assertFalse(assess_proposal(self.proposal, self.capture, self.evidence).eligible)
        with self.assertRaises(ValueError):
            self.fold(self.event())
        self.setUp()
        self.capture.capture_id = "wrong"
        with self.assertRaisesRegex(ValueError, "binding_mismatch"):
            self.fold(self.event())
        self.setUp()
        unsafe_event = DecisionEvent.model_construct(
            **(self.event().model_dump() | {"decision": "execute"}))
        with self.assertRaises(ValueError):
            self.fold(unsafe_event)
        for digest in ("B" * 64, "", "x" * 64):
            self.digest = digest
            with self.assertRaises(ValueError):
                self.fold()
        self.setUp()
        before = [obj.model_dump_json() for obj in (self.proposal, self.capture, *self.evidence)]
        projection = self.fold(self.event(), self.event("authorize_dry_run", 1))
        unsafe = projection.model_copy(update={"real_execution_allowed": True})
        self.assertFalse(unsafe.real_execution_allowed)
        unsafe = type(projection).model_construct(real_execution_allowed=True)
        self.assertFalse(unsafe.real_execution_allowed)
        self.assertEqual(before, [obj.model_dump_json() for obj in
                                 (self.proposal, self.capture, *self.evidence)])
        with self.assertRaises(TypeError):
            fold_decisions(self.proposal, self.capture, self.evidence, self.digest, (), eligible=True)
