"""Meaningful checks for design weighting, upstream data reuse, and test gates."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import train as pipeline


class ProtocolTests(unittest.TestCase):
    def test_each_design_gets_equal_weight_with_mean_one(self):
        frame = pd.DataFrame({'design':['large']*4+['small1','small2']})
        weights = pipeline.design_weights(frame)
        self.assertAlmostEqual(weights.mean(),1)
        for name in frame['design'].unique():
            self.assertAlmostEqual(weights[frame['design']==name].sum(),2)

    def test_macro_error_is_not_pooled_error(self):
        frame = pd.DataFrame({'design':['large']*4+['small1','small2'],
                              'family':['a']*4+['b','c'], 'label_arrival_ns':[1.]*6})
        prediction = np.array([2.]*4+[11.,11.])
        result = pipeline.evaluate(frame,prediction)
        self.assertAlmostEqual(result['macro']['mae_ns'],7.)
        self.assertAlmostEqual(result['pooled']['mae_ns'],4.)
        weights = pipeline.design_weights(frame)
        self.assertAlmostEqual(np.average(abs(prediction-1),weights=weights),7.)
        self.assertIsNone(result['macro']['r2'])
        self.assertIsNone(result['macro']['pearson_r'])

    def test_unmodified_upstream_training_receives_all_12_designs(self):
        features = json.loads((ROOT/'feature_columns.json').read_text())
        names = ['design'+str(i) for i in range(12)]
        rows = []
        for i,name in enumerate(names):
            for j in range(2):
                rows.append({'design':name,'rtl_bit':'r['+str(j)+']',
                             'label_arrival_ns':100+i+j/10,
                             **{c:float(i*2+j+k/100) for k,c in enumerate(features)}})
        frame = pd.DataFrame(rows)
        observed = {}
        class FakeEstimator:
            def __init__(self,**kwargs): observed['kwargs']=kwargs
            def fit(self,X,y):
                observed['X']=X.to_numpy(); observed['y']=y.to_numpy().ravel()
                self.n_features_in_=X.shape[1]
                return self
            def get_booster(self): return SimpleNamespace(num_boosted_rounds=lambda:500)
        with tempfile.TemporaryDirectory() as directory:
            ctx = {'features':features,'threads':25,'partition':{'designs':{'train':names}},
                   'protocol':json.loads((ROOT/'protocol.json').read_text())}
            with patch('xgboost.XGBRegressor',FakeEstimator),redirect_stdout(io.StringIO()):
                pipeline.train_upstream(ctx,frame,Path(directory))
            np.testing.assert_array_equal(observed['X'],frame[features].to_numpy(float))
            np.testing.assert_array_equal(observed['y'],frame['label_arrival_ns'].to_numpy(float))
            self.assertEqual(observed['kwargs'],{'n_estimators':500,'max_depth':50,'nthread':25})

    def test_weighting_only_preserves_constructor_rows_targets_and_rounds(self):
        features = json.loads((ROOT/'feature_columns.json').read_text())
        names = ['design'+str(i) for i in range(12)]
        frame = pd.DataFrame([
            {'design':name, 'rtl_bit':f'r[{j}]', 'label_arrival_ns':1+i+j/10,
             **{f:float(i+j+k/100) for k,f in enumerate(features)}}
            for i,name in enumerate(names) for j in range(i+1)])
        for threads in (25, 2):
            calls = []
            class RecordingEstimator:
                def __init__(self, **kwargs):
                    self.call = {'constructor':kwargs}; calls.append(self.call)
                def fit(self, X, y, **kwargs):
                    self.call.update(X=np.asarray(X), y=np.asarray(y).ravel(), fit=kwargs)
                    self.n_features_in_ = X.shape[1]
                    return self
                def get_booster(self):
                    return SimpleNamespace(num_boosted_rounds=lambda:500)
            ctx = {'features':features, 'threads':threads,
                   'partition':{'designs':{'train':names}},
                   'protocol':json.loads((ROOT/'protocol.json').read_text())}
            with tempfile.TemporaryDirectory() as folder:
                with patch('xgboost.XGBRegressor',RecordingEstimator),redirect_stdout(io.StringIO()):
                    pipeline.train_upstream(ctx,frame,Path(folder))
                    pipeline.train_weighted(ctx,frame)
            a,b = calls
            self.assertEqual(a['constructor'], b['constructor'])
            self.assertEqual(b['constructor'],dict(n_estimators=500,max_depth=50,nthread=threads))
            np.testing.assert_array_equal(a['X'],b['X'])
            np.testing.assert_array_equal(a['y'],b['y'])
            self.assertEqual(a['fit'],{})
            self.assertEqual(set(b['fit']),{'sample_weight'})
            np.testing.assert_array_equal(b['fit']['sample_weight'],pipeline.design_weights(frame))

    def test_test_requires_selection_before_reading_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(pipeline,'read_partition',side_effect=AssertionError('Test rows read too early')):
                with self.assertRaisesRegex(RuntimeError,'Run compare'):
                    pipeline.final_test({'out':Path(directory)})

    def test_training_closed_after_test_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'evaluation_lock.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'training is closed'):
                pipeline.fit_worker({'out':root},'baseline')

    def test_changed_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'result.txt'
            path.write_text('original')
            hashes = {'result.txt':pipeline.sha(path)}
            path.write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'Missing or changed'):
                pipeline.verify_files(root,hashes)


if __name__ == '__main__':
    unittest.main()
