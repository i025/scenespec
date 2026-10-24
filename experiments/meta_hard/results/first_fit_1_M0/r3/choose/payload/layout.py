"""Initial fixed policy, evaluated as DATA in the restricted runner, never imported."""


def layout(packet):
    if packet['phase'] == 'choose':
        return {'experiment':'random', 'analysis':'Inspect the fixed random-map dev probe.'}
    examples = packet['examples']
    selected = []
    for solvable in (False, True):
        selected.extend([i for i, row in enumerate(examples) if row['solvable'] == solvable][:3])
    return {'analysis':'Compare generated successes and failures; improve general first-output solvability.',
            'example_indices':selected,
            'prompt':'Use the dev feedback to improve the general construction algorithm. Preserve diversity '
                     'and useful task difficulty. Return a complete replacement, not individual scene edits.'}
