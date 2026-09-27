from app.decision.learning import fit, shadow


def row(prompt, local, step, split='train'):
    return {'prompt':prompt, 'split':split,'candidates':[
        {'candidate':'local','error':None,'checks':{'passed':local}},
        {'candidate':'step','error':None,'checks':{'passed':step}}]}


def test_single_class_not_claimed_as_discriminative():
    model = fit([row('x', False, True)])
    assert all(v['status'] == 'single_class_unidentifiable' for v in model['models'].values())
    assert shadow('x', model, ['local'])['raw_quality_scores'] == {}


def test_training_excludes_calibration_and_errors_and_shadow_cannot_authorize():
    rows = [row('a', False, True), row('1234', True, False)]
    bad = row('invalid', True, True)
    bad['candidates'][0]['error'] = 'transport'
    model = fit(rows)
    assert model == fit(rows + [bad, row('hidden', True, True, 'test')])
    result = shadow('1234', model, ['local'])
    assert set(result['raw_quality_scores']) == {'local'}
    assert result['executed_action_changed'] is False
