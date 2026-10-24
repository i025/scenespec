"""One raw layout proposal. Called only inside the restricted experiment runner.

The trusted controller supplies the original RNG sample and enforces the
256-proposal ceiling, geometry and minimum crate/goal distance. No oracle here.
"""


def layout(packet):
    sample = packet['sample']
    return {'obstacles': sample[:-3], 'agent': sample[-3],
            'crate': sample[-2], 'goal': sample[-1]}
