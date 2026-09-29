"""Checks for selection/refit semantics and protection of previous results."""
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
import train
import common as c


class EarlyStoppingTests(unittest.TestCase):
    def test_negative_r_squared_is_valid(self):
        result = c.metric_values(np.array([1.,2.,3.]), np.array([3.,3.,3.]))
        self.assertAlmostEqual(result['r2'],-1.5)

    def test_weighted_validation_mae_equals_macro_not_pooled(self):
        frame = pd.DataFrame({'design':['large']*4+['small'], 'family':['a']*4+['b'],
                              'label_arrival_ns':[1.]*5})
        predicted = np.array([2.]*4+[11.])
        weights = c.design_weights(frame)
        stats = c.evaluate(frame,predicted)
        self.assertAlmostEqual(np.average(abs(predicted-1),weights=weights),5.5)
        self.assertAlmostEqual(stats['macro']['mae_ns'],5.5)
        self.assertNotAlmostEqual(stats['pooled']['mae_ns'],5.5)

    def test_search_uses_validation_weights_and_refit_is_fresh_train_only(self):
        def frame(designs):
            n=len(designs)
            return pd.DataFrame({'design':designs, 'family':designs,
                                 'feature':np.arange(n,dtype=float),
                                 'label_arrival_ns':np.arange(n,dtype=float)})
        training=frame(['a']*4+['b']*2)
        validation=frame(['v1']*3+['v2'])
        instances=[]
        class FakeEstimator:
            def __init__(self,**params):
                self.params=params; instances.append(self)
                self.best_iteration=2; self.best_score=1.
            def fit(self,X,y,**kwargs):
                self.X=X.copy(); self.y=np.asarray(y).copy(); self.fit_args=kwargs
                return self
            def get_params(self): return self.params
            def get_booster(self):
                return SimpleNamespace(num_boosted_rounds=lambda:4 if 'early_stopping_rounds' in self.params else self.params['n_estimators'])
            def evals_result(self): return {'validation_0':{'mae':[3.,2.,1.,1.5]}}
            def predict(self,X,**kwargs):
                self.predict_args=kwargs
                return X.iloc[:,0].to_numpy()+1
        ctx={'features':['feature'],'threads':25,
             'protocol':{'baseline':{'n_estimators':500,'max_depth':50,'nthread':25},
                         'early_stopping':{'max_estimators':2000,'patience':1,'eval_metric':'mae'}},
             'partition':{'designs':{'train':['a','b'],'validation':['v1','v2']}}}
        with patch('xgboost.XGBRegressor',FakeEstimator):
            search,selection,history=train.choose_rounds(ctx,training,validation)
            final=train.fresh_refit(ctx,training,selection['best_n_estimators'])
        self.assertEqual(selection['best_iteration_zero_based'],2)
        self.assertEqual(selection['best_n_estimators'],3)
        self.assertEqual(selection['rounds_actually_trained'],4)
        self.assertEqual(search.predict_args,{'iteration_range':(0,3)})
        self.assertEqual(len(instances),2)
        self.assertIsNot(search,final)
        self.assertEqual(search.params['n_estimators'],2000)
        self.assertEqual(final.params,{'n_estimators':3,'max_depth':6,'nthread':25,'learning_rate':0.05})
        np.testing.assert_array_equal(search.fit_args['sample_weight'],c.design_weights(training))
        np.testing.assert_array_equal(search.fit_args['sample_weight_eval_set'][0],c.design_weights(validation))
        np.testing.assert_array_equal(search.fit_args['eval_set'][0][1],validation.label_arrival_ns)
        self.assertEqual(set(final.fit_args),{'sample_weight'})
        np.testing.assert_array_equal(final.X,search.X)
        np.testing.assert_array_equal(final.y,training.label_arrival_ns)

    def test_protected_directories_cannot_be_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); old=root/'old'; data=root/'data'
            for output in [old,old/'child',root,data,data/'child']:
                with self.assertRaisesRegex(RuntimeError,'separate'):
                    train.separate_paths(old,data,output)
            train.separate_paths(old,data,root/'new')

    def test_depth_cannot_change_with_tree_count(self):
        with self.assertRaisesRegex(RuntimeError,'Unexpected estimator'):
            train.check_parameter_changes({'max_depth':50,'n_estimators':500},
                                          {'max_depth':4,'n_estimators':20},{'n_estimators'})

    def test_test_requires_selection_before_reading_rows(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(c,'read_partition',side_effect=AssertionError('Read too soon')):
                with self.assertRaisesRegex(RuntimeError,'Run compare'):
                    train.final_test({'out':Path(d)})

    def test_round_search_and_refit_close_after_test_lock(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'evaluation_lock.json').write_text('{}')
            for which in ['round_search','weighted_es']:
                with self.assertRaisesRegex(RuntimeError,'closed'):
                    train.fit_worker({'out':root},which)


if __name__ == '__main__': unittest.main()
