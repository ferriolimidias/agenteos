"""Server-owned synthetic PT-BR examples, not independent human calibration."""
EXAMPLES = [
    {'id': 'schedule_multi', 'title': 'Preço, disponibilidade e agendamento', 'text': 'Qual o valor? Há disponibilidade na sexta? Quero agendar.', 'expected_intents': ['price', 'availability', 'booking']},
    {'id': 'multi_price_trust', 'title': 'Preço, condições e confiança', 'text': 'Qual o preço? Posso parcelar no cartão? Tenho receio de golpe, existe garantia?', 'expected_intents': ['price', 'conditions', 'objection', 'trust']},
    {'id': 'close_human', 'title': 'Fechamento com atendimento humano', 'text': 'Quero comprar, pode mandar o link? Antes quero falar com um atendente.', 'expected_intents': ['closing', 'human']},
    {'id': 'price_timing', 'title': 'Objeções de preço e momento', 'text': 'Está caro, não posso agora. Talvez mês que vem. Como funciona?', 'expected_intents': ['price', 'objection', 'questions']},
    {'id': 'support_payment', 'title': 'Suporte e condições', 'text': 'Não recebi minha compra, paguei no pix. Qual o prazo de entrega?', 'expected_intents': ['support', 'conditions']},
    {'id': 'uncertain', 'title': 'Abstenção por falta de contexto', 'text': 'Hmm... talvez.', 'expected_intents': []},
    {'id': 'fit_conditions', 'title': 'Adequação e parcelamento', 'text': 'Não sei se serve para mim. Inclui instalação? Posso parcelar?', 'expected_intents': ['questions', 'conditions', 'objection']},
]

def examples():
    return [{'id': item['id'], 'title': item['title'], 'messages': [{'id': f"fixture:{item['id']}:1", 'text': item['text'], 'role': 'customer'}],
             'expected_intents': item['expected_intents'], 'reference_source': 'authored_synthetic_demo'} for item in EXAMPLES]
