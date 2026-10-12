import copy
import math
import pytest
from app.services.jev_classification import demo_classify, provider_classify, questions, suggest_tags, MODEL, STAGES
from app.services.jev_examples import examples


def response(messages, probabilities=None):
    q = questions(messages)
    answers = {key: {'type': 'noul', 'noul': .05} for key in q if q[key]['type'] == 'noul'}
    answers['primary_stage'] = {'type': 'choice', 'choice': 'consideration',
                              'probabilities': {'discovery': .02, 'consideration': .9, 'decision': .02, 'support': .02, 'unknown': .04}, 'confidence': .9}
    if probabilities:
        for key, value in probabilities.items():
            answers[key]['noul'] = value
    return {'model': MODEL, 'answers': answers, 'usage': {'input_tokens': 900, 'output_tokens': 150}}


def test_authored_synthetic_demo_reference_and_abstention():
    for example in examples():
        actual = demo_classify(example['messages'])
        assert {i['key'] for i in actual['intents']} == set(example['expected_intents'])
        assert actual['abstention']['abstained'] == (not bool(example['expected_intents']))
        assert all(item['confidence'] is None for item in actual['intents'])


def test_independent_multi_intents_stage_and_message_evidence():
    messages = [{'id': 'm1', 'text': 'Quanto custa?', 'role': 'customer'}, {'id': 'm2', 'text': 'Quero falar com atendente.', 'role': 'customer'}]
    payload = response(messages, {'price': .95, 'human': .92, 'evidence_price_0': .95, 'evidence_human_1': .9})
    result = provider_classify(payload, questions(messages), messages)
    assert [i['key'] for i in result['intents']] == ['price', 'human']
    assert result['intents'][0]['evidence_ids'] == ['m1']
    assert result['intents'][1]['evidence_ids'] == ['m2']
    assert result['primary_stage']['key'] == 'consideration'
    assert all('excerpt' not in item for item in result['evidence'])


def test_abstention_without_evidence_even_when_global_probability_high():
    messages = examples()[0]['messages']
    result = provider_classify(response(messages, {'price': .99}), questions(messages), messages)
    assert result['intents'] == []
    assert result['abstention']['abstained']
    assert 'price' in result['abstention']['uncertain_keys']

@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, 2, True, '0.9', None])
def test_provider_invalid_probability_fail_closed(value):
    messages = examples()[0]['messages']
    payload = response(messages)
    payload['answers']['price']['noul'] = value
    with pytest.raises(ValueError):
        provider_classify(payload, questions(messages), messages)

@pytest.mark.parametrize('mutation', ['missing_key', 'extra_key', 'wrong_model', 'wrong_type', 'bad_distribution', 'wrong_choice'])
def test_typed_provider_schema_rejects_malformed(mutation):
    messages = examples()[0]['messages']; payload = response(messages)
    if mutation == 'missing_key': payload['answers'].pop('price')
    if mutation == 'extra_key': payload['answers']['made_up'] = {'type':'noul','noul':.9}
    if mutation == 'wrong_model': payload['model'] = 'jev-latest'
    if mutation == 'wrong_type': payload['answers']['price']['type'] = 'score'
    if mutation == 'bad_distribution': payload['answers']['primary_stage']['probabilities']['unknown'] = .9
    if mutation == 'wrong_choice': payload['answers']['primary_stage']['choice'] = 'decision'
    with pytest.raises(ValueError): provider_classify(payload, questions(messages), messages)


def test_only_existing_hypothetical_tags_and_persisted_allowed_stage():
    classification = demo_classify(examples()[0]['messages'])
    tags = [{'id': 'owned', 'name': 'Preço', 'managed_stage': None}, {'id':'managed','name':'Lead','managed_stage':'lead_established'},
            {'id':'purchase','name':'Compra confirmada','managed_stage':'purchase_confirmed'}]
    actual = suggest_tags(classification, tags, {'stages': [{'key':'lead_established', 'monitor_allowed':True}]})
    assert {i['tag_id'] for i in actual['tag_suggestions']} == {'owned', 'managed'}
    assert all(item['hypothetical'] and item['tag_name'] == 'Tag existente' for item in actual['tag_suggestions'])
    no_allowed = suggest_tags(classification, tags, {'stages': []})
    assert {i['tag_id'] for i in no_allowed['tag_suggestions']} == {'owned'}


def test_agent_mentions_cannot_drive_local_customer_classification():
    result = demo_classify([{'id':'a','role':'agent','text':'Preço e pix, quero comprar, atendente'}])
    assert result['intents'] == [] and result['abstention']['abstained']
