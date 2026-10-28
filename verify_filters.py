"""Independently check MATLAB coefficients with the Python standard library."""
import cmath
import math
import re
import struct
from pathlib import Path

source = Path(__file__).with_name('respons_frekuensi_filter_aktual.m').read_text(encoding='utf-8-sig')
def coefficients(name):
    match = re.search(r'\b' + name + r'\s*=\s*double\(single\(\[(.*?)\]\)\)',source,re.S)
    assert match, name
    values = [float(x) for x in re.findall(r'[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?',match[1])]
    return [struct.unpack('<f',struct.pack('<f',value))[0] for value in values]
def magnitude_db(b,a,frequency):
    z = cmath.exp(-2j * math.pi * frequency / 4000)
    response = sum(value*z**i for i,value in enumerate(b)) / sum(value*z**i for i,value in enumerate(a))
    return 20 * math.log10(abs(response))
filters={label:(coefficients('b'+label),coefficients('a'+label)) for label in ('HPF','LPF','Notch')}
for label,(b,a) in filters.items():
    assert len(b)==len(a)==3
    delta = cmath.sqrt(a[1]**2 - 4*a[0]*a[2])
    assert max(abs((-a[1]+sign*delta)/(2*a[0])) for sign in (-1,1)) < 1, label
assert abs(magnitude_db(*filters['HPF'],20) + 3.0103) < 0.05
assert abs(magnitude_db(*filters['LPF'],500) + 3.0103) < 0.05
notch=magnitude_db(*filters['Notch'],50)
assert notch < -40, notch
print(f'PASS: float32 pole stability, 20 Hz HPF cutoff, 500 Hz LPF cutoff and {notch:.2f} dB notch at 50 Hz')
print('This numerical check does not execute MATLAB, open the interactive explorer, or validate hardware sampling.')
