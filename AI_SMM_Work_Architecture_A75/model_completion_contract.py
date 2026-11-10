"""Provider-neutral completion evidence for text/model responses.

This gate validates transport finality only. It never approves semantic content,
spends a budget, changes a model route, or repairs an incomplete response.
"""


def require_completed(transport, response):
    """Return a complete transport response or reject it before content parsing.

    Ollama's older local fixtures may omit done; if supplied it must be
    literally true. An explicit length/interrupt reason is never accepted.
    Chat-completions and Gemini Interactions require affirmative completion.
    """
    if not isinstance(response, dict):
        raise ValueError('Model transport returned a non-object response')
    if transport == 'ollama':
        if ('done' in response and response['done'] is not True) or (
                'done_reason' in response and response['done_reason'] != 'stop'):
            raise ValueError('Ollama model response is incomplete')
        return response
    if transport == 'chat_completions':
        choices = response.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError('Cloud model did not return exactly one completion')
        choice = choices[0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('Cloud model response is incomplete')
        message = choice.get('message')
        if (not isinstance(message, dict) or message.get('refusal') or
                message.get('tool_calls') or message.get('function_call')):
            raise ValueError('Cloud text response contains refusal or unexpected tool call')
        return choice
    if transport == 'gemini_interactions':
        if response.get('status') != 'completed' or response.get('error'):
            raise ValueError('Gemini interaction did not complete')
        return response
    raise ValueError('Unknown model transport completion contract')
