#!/usr/bin/env python3
"""Black-box T01 acceptance harness. No business implementation is included."""
from pathlib import Path
import argparse
import copy
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parent

def check(function, cases):
    results = []
    for case in cases:
        original = copy.deepcopy(case['input'])
        payload = copy.deepcopy(original)
        expected = case['expected']
        try:
            result = function(payload)
        except ValueError:
            passed = expected.get('raises') == 'ValueError'
        except Exception:
            passed = False
        else:
            passed = result == expected and 'raises' not in expected
        passed = passed and payload == original
        results.append({'case_id':case['case_id'],'passed':passed})
    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-root', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    cases = json.loads((ROOT/'material-cases.json').read_text())['cases']
    if args.self_test:
        # Exercise this harness against deliberately broken fixed outputs.
        wrong = check(lambda _: {'status':'complete'}, cases)
        raises = check(lambda _: (_ for _ in ()).throw(ValueError('fixture')), cases)
        assert not any(x['passed'] for x in wrong)
        assert sum(x['passed'] for x in raises) == 9
        print(json.dumps({'status':'PASS','scope':'acceptance_harness_self_test_only','implementation_test_executed':False,'cases':len(cases),'wrong_constant_rejected':len(wrong),'always_raises_valid_case_failures':sum(not x['passed'] for x in raises)}))
        return 0
    if args.candidate_root is None:
        parser.error('specify --candidate-root or --self-test')
    module_path = args.candidate_root.resolve()/'app/insurance_poc/materials.py'
    if not module_path.is_file():
        print(json.dumps({'status':'NOT_IMPLEMENTED','expected_module':str(module_path),'implementation_test_executed':False}))
        return 3
    spec = importlib.util.spec_from_file_location('t01_candidate', module_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(args.candidate_root.resolve()))
    spec.loader.exec_module(module)
    results = check(module.check_material_completeness, cases)
    passed = all(x['passed'] for x in results)
    print(json.dumps({'status':'PASS' if passed else 'FAIL','scope':'business_implementation_cases','implementation_test_executed':True,'results':results}, ensure_ascii=False, indent=2))
    return 0 if passed else 1

if __name__ == '__main__':
    raise SystemExit(main())
