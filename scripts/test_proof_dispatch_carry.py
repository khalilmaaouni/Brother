"""Persist proof liability when the next caller has no proof environment."""
import os,sys,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_proof_dispatch_accounting import ProofDispatch
from plugin.runtime.brother.core import openrouter_ledger as L
from scripts.loop import proof_ledger as P

class Carry(unittest.TestCase):
 setUp=ProofDispatch.setUp
 write_start=ProofDispatch.write_start
 reserve=ProofDispatch.reserve
 marker=ProofDispatch.marker
 rows=ProofDispatch.rows
 def legacy(self):return mock.patch.dict(os.environ,{key:'' for key in self.env})
 def test_tagged_open_survives_missing_policy(self):
  self.reserve(19)
  with self.legacy():
   self.assertEqual(L.current_spend(str(self.state),now=172801),19)
   with self.assertRaises(L.BudgetExceeded):L.reserve(str(self.state),20,2,'next',now=172801)
 def test_tagged_abandoned_survives_missing_policy(self):
  rid=self.reserve(19);self.marker(rid);L.abandon(str(self.state),rid,'crash',now=86402)
  with self.legacy():
   self.assertEqual(L.spend_breakdown(str(self.state),now=172801)['abandoned'],19)
   with self.assertRaises(L.BudgetExceeded):L.reserve(str(self.state),20,2,'next',now=172801)
 def test_old_reserve_new_dispatch_survives_missing_policy(self):
  L.mark_dispatched(str(self.state),'historic','old',now=86401)
  with self.legacy():self.assertEqual(L.current_spend(str(self.state),now=172801),104.86)
 def test_known_settlement_keeps_daily_cap_behavior(self):
  rid=self.reserve(19);self.marker(rid);L.reconcile(str(self.state),rid,.25,now=86402,cost_source='provider_usage')
  with self.legacy():self.assertEqual(L.current_spend(str(self.state),now=172801),0)
 def test_unmarked_historic_legacy_policy_unchanged(self):
  with self.legacy():self.assertEqual(L.current_spend(str(self.state),now=172801),0)
 def test_marker_fsyncs_before_return(self):
  rid=self.reserve()
  with mock.patch.object(L.os,'fsync',wraps=L.os.fsync) as sync:self.marker(rid)
  sync.assert_called_once()
 def test_resolved_path_alias_is_same_ledger(self):
  alias=self.root/'alias';alias.symlink_to(self.state,target_is_directory=True)
  self.start['ledger_path']=str(alias/'openrouter-ledger.jsonl');self.write_start()
  self.assertEqual(P.configuration(str(self.state))['ledger'],str(self.ledger.resolve()))
 def test_other_absolute_ledger_is_refused(self):
  self.start['ledger_path']=str(self.root/'other.jsonl');self.write_start()
  with self.assertRaises(ValueError):self.reserve()

if __name__=='__main__':unittest.main()
