import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest

from training_store import Store, StoreError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        self.metadata = {'code': 'demo', 'title': 'Demo cabinet', 'revision': 'B', 'group_id': 'family-one',
                         'category': 'cabinet', 'allow_storage': True, 'allow_training': True,
                         'allow_ai': True, 'privacy_confirmed': True}
        self.raw = b'%PDF-FAKE-ONLY-FOR-UNIT-TEST'
        self.drawing = self.store.register(self.raw, self.metadata, 2)
        self.payload = {'task_type': 'dimension', 'question': 'What is the width?', 'part_ref': 'panel-A',
                        'ai_value': '800', 'corrected_value': '900', 'unit': 'mm', 'evidence_type': 'direct',
                        'formula': '', 'explanation': 'Dimension line on front view',
                        'evidence': [{'page': 2, 'bbox': [.1, .2, .3, .2], 'view': 'front'}],
                        'author_ref': 'annotator-1'}

    def tearDown(self):
        self.temp.cleanup()

    def annotation(self, **changes):
        payload = copy.deepcopy(self.payload)
        payload.update(changes)
        return self.store.save_annotation(self.drawing['id'], payload)

    def submit(self, annotation):
        return self.store.review_annotation(annotation['id'], {'expected_version': annotation['version'],
             'decision': 'submit', 'reviewer_ref': 'annotator-1', 'comment': '', 'privacy_confirmed': False})

    def approve(self, annotation):
        submitted = self.submit(annotation)
        return self.store.review_annotation(annotation['id'], {'expected_version': submitted['version'],
             'decision': 'approve', 'reviewer_ref': 'reviewer-2', 'comment': 'Checked', 'privacy_confirmed': True})

    def assert_error(self, status, function, *args, **kwargs):
        with self.assertRaises(StoreError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.status, status)

    def test_original_hash_and_bytes_immutable(self):
        original = self.store.pdf_path(self.drawing['id'])
        self.assertEqual(original.read_bytes(), self.raw)
        self.assertEqual(self.drawing['source_hash'], hashlib.sha256(self.raw).hexdigest())
        second = self.store.register(b'%PDF-new-revision', {**self.metadata, 'revision': 'C'}, 1)
        self.assertNotEqual(original, self.store.pdf_path(second['id']))
        self.assertEqual(original.read_bytes(), self.raw)
        self.assert_error(409, self.store.register, b'different', self.metadata, 1)
        self.assertEqual(original.read_bytes(), self.raw)

    def test_storage_and_redaction_are_explicit(self):
        self.assert_error(403, self.store.register, b'x', {**self.metadata, 'revision': 'C', 'allow_storage': False}, 1)
        self.assert_error(403, self.store.register, b'x', {**self.metadata, 'revision': 'C', 'privacy_confirmed': False}, 1)
        self.assert_error(400, self.store.register, b'x', {**self.metadata, 'revision': 'C', 'allow_ai': 'true'}, 1)

    def test_no_filename_path_or_sensitive_metadata_saved(self):
        with self.store._tx() as db:
            row = db.execute('SELECT source_rel,body FROM drawings').fetchone()
            self.assertEqual(row['source_rel'], self.drawing['id']+'.pdf')
            self.assertNotIn('storage_rel', json.loads(row['body']))
        self.assert_error(400, self.store.register, b'x', {**self.metadata, 'revision': '../secret'}, 1)
        self.assert_error(400, self.store.register, b'x', {**self.metadata, 'revision': 'C', 'title': 'person@example.test'}, 1)
        self.assert_error(404, self.store.pdf_path, '../secret')

    def test_duplicate_hash_cannot_cross_group(self):
        self.assert_error(409, self.store.register, self.raw,
                          {**self.metadata, 'revision': 'C', 'group_id': 'different-family'}, 2)
        allowed = self.store.register(self.raw, {**self.metadata, 'revision': 'C'}, 2)
        self.assertEqual(allowed['group_id'], self.drawing['group_id'])

    def test_revisions_cannot_be_moved_to_a_different_group(self):
        self.assert_error(409, self.store.register, b'%PDF-different-revision',
                          {**self.metadata, 'revision':'C', 'group_id':'other-family'}, 1)

    def test_group_assignment_is_immutable_and_applies_to_new_revisions(self):
        self.store.assign_split('family-one', 'test')
        self.assert_error(409, self.store.assign_split, 'family-one', 'train')
        self.assert_error(400, self.store.assign_split, 'family-one', 'unknown')
        new = self.store.register(b'%PDF-other', {**self.metadata, 'revision': 'C'}, 1)
        self.assertEqual(new['split'], 'test')
        self.assertEqual(self.store.get_drawing(self.drawing['id'])['split'], 'test')

    def test_evidence_coordinates_pages_and_nonfinite_are_strict(self):
        for page, bbox in [(0,[0,0,.1,.1]), (3,[0,0,.1,.1]), (1,[-.1,0,.1,.1]),
                          (1,[.9,0,.2,.1]), (1,[0,0,0,.1]), (1,[0,0,math.nan,.1]),
                          (1,[0,0,math.inf,.1]), (True,[0,0,.1,.1]), (1,[False,0,.1,.1])]:
            self.assert_error(400, self.annotation, evidence=[{'page': page, 'bbox': bbox, 'view':'front'}])

    def test_draft_can_be_incomplete_but_submit_needs_evidence(self):
        annotation = self.annotation(evidence=[])
        self.assertEqual(annotation['status'], 'draft')
        self.assert_error(400, self.submit, annotation)
        calculated = self.annotation(evidence_type='calculated', formula='')
        self.assert_error(400, self.submit, calculated)

    def test_workflow_requires_submit_distinct_reviewer_and_privacy(self):
        annotation = self.annotation()
        request = {'expected_version': 1, 'decision':'approve', 'reviewer_ref':'reviewer-2', 'privacy_confirmed':True}
        self.assert_error(409, self.store.review_annotation, annotation['id'], request)
        submitted = self.submit(annotation)
        request['expected_version'] = submitted['version']
        request['reviewer_ref'] = 'annotator-1'
        self.assert_error(403, self.store.review_annotation, annotation['id'], request)
        request['reviewer_ref'] = 'reviewer-2'
        request['privacy_confirmed'] = False
        self.assert_error(403, self.store.review_annotation, annotation['id'], request)
        request['privacy_confirmed'] = True
        approved = self.store.review_annotation(annotation['id'], request)
        self.assertEqual(approved['status'], 'approved')
        self.assertEqual(approved['version'], 3)

    def test_stale_edit_and_review_fail(self):
        annotation = self.annotation()
        updated = self.store.save_annotation(self.drawing['id'], {**self.payload, 'id':annotation['id'], 'expected_version':1})
        self.assertEqual(updated['version'], 2)
        self.assert_error(409, self.store.save_annotation, self.drawing['id'],
                          {**self.payload, 'id':annotation['id'], 'expected_version':1})
        self.assert_error(409, self.store.review_annotation, annotation['id'],
                          {'expected_version':1, 'decision':'submit', 'reviewer_ref':'annotator-1'})

    def test_history_is_snapshot_and_editing_approval_resets_status(self):
        annotation = self.approve(self.annotation())
        self.store.assign_split('family-one', 'train')
        self.assertEqual(len(self.store.export_samples()['samples']), 1)
        updated = self.store.save_annotation(self.drawing['id'],
            {**self.payload, 'id':annotation['id'], 'expected_version':annotation['version'], 'corrected_value':'950'})
        self.assertEqual(updated['status'], 'draft')
        self.assertEqual(self.store.export_samples()['samples'], [])
        history = self.store.history(annotation['id'])
        self.assertEqual([row['event'] for row in history], ['create','submit','approve','edit'])
        self.assertEqual(history[2]['snapshot']['corrected_value'], '900')
        self.assertEqual(history[3]['snapshot']['corrected_value'], '950')

    def test_concurrent_stale_edit_only_one_wins(self):
        annotation = self.annotation()
        barrier = threading.Barrier(2)
        outcomes = []
        def edit():
            barrier.wait()
            try:
                self.store.save_annotation(self.drawing['id'], {**self.payload, 'id':annotation['id'], 'expected_version':1})
                outcomes.append(200)
            except StoreError as error:
                outcomes.append(error.status)
        threads = [threading.Thread(target=edit) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(outcomes), [200,409])

    def test_export_excludes_unreviewed_unassigned_and_inferred(self):
        self.annotation()
        approved = self.approve(self.annotation())
        result = self.store.export_samples()
        self.assertEqual(result['excluded'], {'not_approved':1, 'split_unassigned':1})
        self.store.assign_split('family-one', 'dev')
        self.approve(self.annotation(evidence_type='inferred'))
        result = self.store.export_samples()
        self.assertEqual(len(result['samples']), 1)
        self.assertEqual(result['samples'][0]['example_id'], approved['id'])
        self.assertEqual(result['excluded'], {'not_approved':1, 'inferred':1})

    def test_export_allowlist_and_latest_consent(self):
        self.approve(self.annotation())
        self.store.assign_split('family-one', 'train')
        sample = self.store.export_samples()['samples'][0]
        encoded = json.dumps(sample)
        for excluded in ('author_ref', 'reviewer_ref', 'title', 'source_rel', 'ai_value', 'model_version'):
            self.assertNotIn(excluded, sample)
        self.assertNotIn('annotator-1', encoded)
        self.assertNotIn(str(self.store.root), encoded)
        self.assertEqual(sample['target']['value'], '900')
        self.store.set_consent(self.drawing['id'], {'allow_training':False})
        self.assertEqual(self.store.export_samples()['samples'], [])
        self.store.set_consent(self.drawing['id'], {'allow_training':True, 'privacy_confirmed':False})
        self.assertEqual(self.store.export_samples()['samples'], [])
        self.store.set_consent(self.drawing['id'], {'privacy_confirmed':True})
        self.assertEqual(len(self.store.export_samples()['samples']), 1)
        with self.store._tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM consent_history').fetchone()[0], 4)

    def test_insufficient_still_needs_image_context_and_can_be_reviewed(self):
        annotation = self.annotation(evidence_type='insufficient', corrected_value='', unit='', evidence=[],
                                     explanation='The drawing does not show this dimension')
        self.assert_error(400, self.submit, annotation)
        annotation = self.annotation(evidence_type='insufficient', corrected_value='', unit='',
                                     evidence=[{'page':1, 'bbox':[0,0,1,1], 'view':'full-page'}],
                                     explanation='The drawing does not show this dimension')
        self.approve(annotation)
        self.store.assign_split('family-one', 'test')
        sample = self.store.export_samples()['samples'][0]
        self.assertEqual(sample['target']['evidence_type'], 'insufficient')
        self.assertEqual(sample['target']['value'], '')
        self.assertEqual(sample['evidence'][0]['bbox'], [0,0,1,1])

    def test_prediction_consent_and_cross_drawing_reference(self):
        self.store.set_consent(self.drawing['id'], {'allow_ai':False})
        self.assert_error(403, self.store.add_prediction, self.drawing['id'], {'answer':'900'}, 'model', 'v1')
        self.store.set_consent(self.drawing['id'], {'allow_ai':True})
        prediction = self.store.add_prediction(self.drawing['id'], {'answer':'900'}, 'model', 'v1')
        annotation = self.annotation(ai_prediction_id=prediction['id'])
        self.assertEqual(annotation['ai_prediction_id'], prediction['id'])
        other = self.store.register(b'%PDF-other', {**self.metadata, 'revision':'C'}, 1)
        self.assert_error(400, self.store.save_annotation, other['id'], {**self.payload, 'ai_prediction_id':prediction['id']})
        self.assert_error(400, self.store.add_prediction, self.drawing['id'], {'answer':math.nan}, 'model', 'v1')
        self.assert_error(400, self.store.add_prediction, self.drawing['id'], {'email':'person@example.test'}, 'model', 'v1')

    def test_rejection_and_changes_requested_require_reason(self):
        submitted = self.submit(self.annotation())
        request = {'expected_version':submitted['version'], 'decision':'changes_requested', 'reviewer_ref':'reviewer-2'}
        self.assert_error(400, self.store.review_annotation, submitted['id'], request)
        request['comment'] = 'Check source dimension again'
        returned = self.store.review_annotation(submitted['id'], request)
        self.assertEqual(returned['status'], 'changes_requested')
        edited = self.store.save_annotation(self.drawing['id'],
                  {**self.payload, 'id':returned['id'], 'expected_version':returned['version']})
        self.assertEqual(edited['status'], 'draft')

    def test_stats_and_unsupported_types_are_bounded(self):
        self.annotation()
        stats = self.store.stats()
        self.assertEqual(stats['drawings'], 1)
        self.assertEqual(stats['annotations'], 1)
        self.assertEqual(stats['training_ready'], 0)
        for task_type in ([], {}, 'unsupported'):
            self.assert_error(400, self.annotation, task_type=task_type)
        self.assert_error(400, self.annotation, question='x'*2001)
        self.assert_error(400, self.annotation, explanation='person@example.test')


if __name__ == '__main__':
    unittest.main()
