# Local verification

Verified on 28 October 2026 using Python 3.14.

`python raw_capture_logger.py --self-test` passed the 8000-record binary-frame check, implicit sample indices, output handling, CRC corruption and case checks, status parsing, unknown metadata handling and serial-preamble scanning. It uses synthetic frames and opens no serial hardware connection.

`python verify_filters.py` independently read the coefficients from the MATLAB source, quantized them to float32, and passed pole-stability checks for all three biquads, the 20 Hz HPF and 500 Hz LPF cutoff checks, and a modeled 50 Hz notch of -90.02 dB.

The MATLAB/Simulink batch used for this publication produced no verification output and ended after about ten minutes with `0xc0000374, Heap corruption`. No running MATLAB process remained when the timeout check ran. MATLAB script execution and the interactive explorer therefore remain unverified. The independent Python check does not establish hardware sampling frequency, actual noise suppression, acquisition calibration, or clinical validity. No participant recordings are included.
