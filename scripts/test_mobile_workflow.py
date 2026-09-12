"""Discriminating native workflow guards with deterministic tool fixtures.

These fixtures prove orchestration and refusal behavior, not Xcode compatibility.
A real native run is recorded separately before delivery.
"""
import base64
import copy
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mobile_workflow as M

SIM = "11111111-2222-3333-4444-555555555555"
TEST = "test://com.apple.xcode/Sample/SampleTests/RoomTests/testOpen"

TOOL = r'''#!/usr/bin/env python3
import base64,json,os,pathlib,plistlib,shutil,sys
args=sys.argv[1:]; root=pathlib.Path(os.environ['FIXTURE_ROOT']); app=root/'built.app'; installed=root/'installed.app'
if pathlib.Path(sys.argv[0]).name=='xcodebuild':
 if '-version' in args: print('Xcode fixture'); sys.exit(0)
 if '-showBuildSettings' in args:
  rows=[{'target':'Sample','buildSettings':{'TARGET_BUILD_DIR':str(root),'FULL_PRODUCT_NAME':'built.app'}}]
  if os.environ.get('FIXTURE_AMBIGUOUS'): rows*=2
  print(json.dumps(rows));sys.exit(0)
 if 'test' in args:
  if os.environ.get('FIXTURE_BUILD_FAIL'): print('build failed');sys.exit(65)
  bundle=pathlib.Path(args[args.index('-resultBundlePath')+1]);bundle.mkdir()
  leaf={'nodeType':'Test Case','nodeIdentifierURL':'test://com.apple.xcode/Sample/SampleTests/RoomTests/testOpen','result':os.environ.get('FIXTURE_TEST_RESULT','Passed')}
  (bundle/'tests.json').write_text(json.dumps({'testNodes':[leaf]}));(bundle/'marker').touch()
  if os.environ.get('FIXTURE_SOURCE_CHANGE'): pathlib.Path('base.txt').write_text('changed')
  if os.environ.get('FIXTURE_STAMP'):
   data=plistlib.loads((app/'Info.plist').read_bytes())
   data['SourceRevision']=next(a.split('=',1)[1] for a in args if a.startswith('SOURCE_REVISION='))
   (app/'Info.plist').write_bytes(plistlib.dumps(data))
  print('fixture test completed');sys.exit(0)
if args[:1]==['--find']: print('/fixture/'+args[-1]);sys.exit(0)
if args[:3]==['xcresulttool','get','test-results']:
 print((pathlib.Path(args[args.index('--path')+1])/'tests.json').read_text());sys.exit(0)
if args[:4]==['simctl','list','devices','available']:
 devices=[] if os.environ.get('FIXTURE_NO_DEVICE') else [{'udid':'11111111-2222-3333-4444-555555555555','state':'Booted','name':'Fixture Phone'}]
 print(json.dumps({'devices':{'iOS-fixture':devices}}));sys.exit(0)
if args[:2] in (['simctl','boot'],['simctl','bootstatus']):sys.exit(0)
if args[:2]==['simctl','install']:
 (root/'install-called').write_text('yes')
 if installed.exists():shutil.rmtree(installed)
 shutil.copytree(app,installed)
 if os.environ.get('FIXTURE_WRONG_INSTALL'):
  data=plistlib.loads((installed/'Info.plist').read_bytes());data['CFBundleVersion']='old';(installed/'Info.plist').write_bytes(plistlib.dumps(data))
 sys.exit(0)
if args[:2]==['simctl','get_app_container']:print(installed);sys.exit(0)
if args[:2]==['simctl','launch']:print('sample: 123');sys.exit(0)
if args[:2]==['simctl','io']:
 path=pathlib.Path(args[-1])
 if os.environ.get('FIXTURE_BAD_PNG'): path.write_text('not png')
 else:path.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII='))
 sys.exit(0)
print('unexpected fixture command',args);sys.exit(99)
'''


class MobileWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-mobile-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Fake native tools must not contend with a real machine-wide run.
        self.lock_directory = patch.object(M.tempfile, "gettempdir", return_value=str(self.root))
        self.lock_directory.start(); self.addCleanup(self.lock_directory.stop)
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "base.txt").write_text("base")
        (self.repo / "Sample.xcodeproj").mkdir()
        (self.repo / "Sample.xcodeproj/project.pbxproj").write_text("fixture")
        self.git("add", "."); self.git("commit", "-qm", "base")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.archive = self.make_app("reference.app")
        self.make_app("built.app")
        self.observation = self.root / "phone.json"
        self.observation.write_text(json.dumps({"result":{"apps":[{"bundleIdentifier":"example.Sample","version":"1.0","bundleVersion":"1"}]}}))
        self.mapping = self.root / "mapping.md"; self.mapping.write_text("Recorded release source " + self.sha)
        self.reference = self.root / "reference.json"
        M.record_reference(self.repo, self.archive, self.observation, self.mapping, self.sha, self.reference)
        self.bin = self.root / "bin"; self.bin.mkdir()
        for name in ("xcodebuild", "xcrun"):
            path = self.bin / name; path.write_text(TOOL); path.chmod(0o755)
        self.environment = patch.dict(os.environ, {"PATH":str(self.bin)+os.pathsep+os.environ["PATH"], "FIXTURE_ROOT":str(self.root)})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.profile = {"schema":M.PROFILE_SCHEMA,"project":"Sample.xcodeproj","scheme":"Sample","target":"Sample",
                        "configuration":"Debug","simulator_id":SIM,"bundle_id":"example.Sample","version":"1.0","build":"1",
                        "expected_tests":[TEST],"only_testing":["SampleTests/RoomTests/testOpen"],"timeout_seconds":20}
        self.profile_path = self.root / "profile.json"

    def test_second_native_run_cannot_take_an_owned_lease(self):
        with M.lease():
            with self.assertRaises(M.Refusal) as raised:
                with M.lease():
                    self.fail("A second owner entered the native lease")
        self.assertEqual(raised.exception.status, "NO-DATA")

    def git(self, *args):
        return subprocess.check_output(["git","-C",str(self.repo),*args],text=True,stderr=subprocess.DEVNULL)

    def make_app(self, name):
        path = self.root/name; path.mkdir()
        (path/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'example.Sample','CFBundleShortVersionString':'1.0','CFBundleVersion':'1','CFBundleExecutable':'Sample'}))
        (path/'Sample').write_bytes(b'fixture executable')
        return path

    def run_flow(self, **environment):
        profile = copy.deepcopy(self.profile);profile['environment']=environment
        self.profile_path.write_text(json.dumps(profile))
        return M.run_workflow(self.repo,self.profile_path,self.reference,self.root/'run')

    def test_reference_binds_observation_mapping_and_source(self):
        ref,current = M.verify_reference(self.reference,self.repo)
        self.assertEqual(ref['base_revision'],current['revision'])
        self.assertEqual(ref['association'],'recorded-release-mapping')
        self.assertIn('not an embedded',ref['limits'][0])

    def test_reference_wrong_phone_version_refuses_before_writing(self):
        data=json.loads(self.observation.read_text());data['result']['apps'][0]['bundleVersion']='other';self.observation.write_text(json.dumps(data))
        output=self.root/'wrong.json'
        with self.assertRaisesRegex(M.Refusal,'version/build differ'):
            M.record_reference(self.repo,self.archive,self.observation,self.mapping,self.sha,output)
        self.assertFalse(output.exists())

    def test_reference_duplicate_app_observation_is_ambiguous(self):
        data=json.loads(self.observation.read_text());data['result']['apps']*=2
        with self.assertRaisesRegex(M.Refusal,'exactly one'):
            M.app_observation(data,'example.Sample')

    def test_changed_mapping_and_reference_binary_refuse(self):
        original=self.mapping.read_bytes();self.mapping.write_text('different record')
        with self.assertRaisesRegex(M.Refusal,'Evidence changed'):M.verify_reference(self.reference,self.repo)
        self.mapping.write_bytes(original);(self.archive/'Sample').write_bytes(b'different binary')
        with self.assertRaisesRegex(M.Refusal,'executable changed'):M.verify_reference(self.reference,self.repo)

    def test_candidate_without_reference_ancestry_refuses_before_tools(self):
        (self.repo/'base.txt').write_text('working reference');self.git('add','.');self.git('commit','-qm','reference')
        later=self.git('rev-parse','HEAD').strip();other=self.root/'later-reference.json'
        M.record_reference(self.repo,self.archive,self.observation,self.mapping,later,other)
        self.git('checkout','--detach',self.sha)
        with self.assertRaisesRegex(M.Refusal,'does not descend'):M.verify_reference(other,self.repo)
        self.assertFalse((self.root/'install-called').exists())

    def test_descendant_is_allowed_but_not_called_behavioral_equivalence(self):
        (self.repo/'new.txt').write_text('new');self.git('add','.');self.git('commit','-qm','candidate')
        ref,current=M.verify_reference(self.reference,self.repo)
        self.assertNotEqual(ref['base_revision'],current['revision'])

    def test_dirty_candidate_and_repo_output_refuse(self):
        (self.repo/'base.txt').write_text('dirty')
        with self.assertRaisesRegex(M.Refusal,'clean committed'):M.verify_reference(self.reference,self.repo)
        self.git('restore','base.txt')
        with self.assertRaisesRegex(M.Refusal,'outside'):M.record_reference(self.repo,self.archive,self.observation,self.mapping,self.sha,self.repo/'reference.json')

    def test_malformed_reference_fields_fail_without_traceback_types(self):
        original=json.loads(self.reference.read_text())
        for key,value in [('identity',[]),('observation',None),('base_revision',{}),('limits',None)]:
            data=copy.deepcopy(original);data[key]=value;self.reference.write_text(json.dumps(data))
            with self.assertRaises(M.Refusal):M.verify_reference(self.reference,self.repo)

    def test_profile_rejects_source_redirection_and_missing_expected_tests(self):
        cases=[('environment',{'PATH':'/wrong'}),('project','../Other.xcodeproj'),('expected_tests',[]),('simulator_id','booted'),('build_wrapper','../outside')]
        for key,value in cases:
            data=copy.deepcopy(self.profile);data[key]=value
            with self.assertRaises(M.Refusal):M.validate_profile(data,self.repo)

    def test_full_fixture_workflow_binds_test_install_capture_and_preserves_uncertainty(self):
        report,receipt=self.run_flow()
        self.assertEqual(report['technical_status'],'PASS',report.get('reason'))
        self.assertTrue(receipt.is_file())
        self.assertEqual(report['built_app']['executable']['sha256'],report['installed_app']['executable']['sha256'])
        self.assertEqual(report['screenshot']['width'],1)
        self.assertEqual(report['observations']['human_acceptance'],'NO-DATA')
        self.assertEqual(report['observations']['embedded_source_stamp'],'NO-DATA')
        self.assertFalse(any('-derivedDataPath' in s.get('argv',[]) for s in report['stages']))

    def test_skipped_test_prevents_installation(self):
        report,_=self.run_flow(FIXTURE_TEST_RESULT='Skipped')
        self.assertEqual(report['technical_status'],'FAIL')
        self.assertFalse((self.root/'install-called').exists())

    def test_nonzero_native_command_cannot_pass(self):
        report,_=self.run_flow(FIXTURE_BUILD_FAIL='1')
        self.assertEqual(report['technical_status'],'FAIL')
        self.assertFalse((self.root/'install-called').exists())

    def test_changed_source_during_tests_refuses(self):
        report,_=self.run_flow(FIXTURE_SOURCE_CHANGE='1')
        self.assertEqual(report['technical_status'],'FAIL')
        self.assertIn('candidate',report['reason'])
        self.assertFalse((self.root/'install-called').exists())

    def test_missing_selected_device_is_no_data(self):
        report,receipt=self.run_flow(FIXTURE_NO_DEVICE='1')
        self.assertEqual(report['technical_status'],'NO-DATA')
        self.assertTrue(receipt.is_file())
        self.assertFalse((self.root/'install-called').exists())

    def test_wrong_installed_build_is_failure(self):
        report,_=self.run_flow(FIXTURE_WRONG_INSTALL='1')
        self.assertEqual(report['technical_status'],'FAIL')
        self.assertIn('Installed app differs',report['reason'])

    def test_ambiguous_target_is_rejected_before_install(self):
        report,_=self.run_flow(FIXTURE_AMBIGUOUS='1')
        self.assertEqual(report['technical_status'],'FAIL')
        self.assertFalse((self.root/'install-called').exists())

    def test_bad_capture_cannot_be_accepted(self):
        report,_=self.run_flow(FIXTURE_BAD_PNG='1')
        self.assertEqual(report['technical_status'],'FAIL')
        self.assertIn('not a PNG',report['reason'])

    def test_required_embedded_source_stamp_checked_in_real_plist(self):
        self.profile['source_stamp']={'build_setting':'SOURCE_REVISION','plist_key':'SourceRevision'}
        report,_=self.run_flow(FIXTURE_STAMP='1')
        self.assertEqual(report['technical_status'],'PASS',report.get('reason'))
        self.assertEqual(report['built_app']['source_stamp'],self.sha)

    def test_missing_required_source_stamp_prevents_install(self):
        self.profile['source_stamp']={'build_setting':'SOURCE_REVISION','plist_key':'SourceRevision'}
        report,_=self.run_flow()
        self.assertEqual(report['technical_status'],'FAIL')
        self.assertFalse((self.root/'install-called').exists())

    def test_handoff_contains_result_and_empty_markers_and_detects_tamper(self):
        report,_=self.run_flow();self.assertEqual(report['technical_status'],'PASS')
        output=self.root/'handoff.zip';summary=M.pack(self.root/'run',output)
        self.assertEqual(summary['status'],'PASS')
        with zipfile.ZipFile(output) as z:payload={n:z.read(n) for n in z.namelist()}
        self.assertIn('tests.xcresult/marker',payload)
        payload['preview.png']=b'changed'
        broken=self.root/'broken.zip'
        with zipfile.ZipFile(broken,'w') as z:
            for name,content in payload.items():z.writestr(name,content)
        with self.assertRaisesRegex(M.Refusal,'content differs'):M.verify_pack(broken)

    def test_handoff_rejects_unsafe_member_paths(self):
        path=self.root/'unsafe.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('../escape','bad')
        with self.assertRaisesRegex(M.Refusal,'Unsafe'):M.verify_pack(path)

    def test_handoff_rejects_links(self):
        self.run_flow();os.symlink(self.mapping,self.root/'run'/'linked')
        with self.assertRaisesRegex(M.Refusal,'symlink'):M.pack(self.root/'run',self.root/'linked.zip')

    def test_doctor_lists_project_and_tool_probe_without_certifying_mcp(self):
        result=M.doctor(self.repo,self.root/'doctor.json')
        self.assertEqual(result['status'],'PASS')
        self.assertIn('Sample.xcodeproj',result['projects'])
        self.assertIn('not MCP connectivity',result['limits'][0])


if __name__ == '__main__':
    unittest.main()
