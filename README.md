# EMG Signal Tools

Source-only tools for digital-filter inspection and the STM32 EMG capture protocol. No human recordings or participant documents are included.

`respons_frekuensi_filter_aktual.m` evaluates the stored float32 filter coefficients at a nominal 4000 Hz sampling rate and checks pole stability. `filter_response_live.m` provides an interactive MATLAB explorer. MATLAB and Signal Processing Toolbox are required for the filter plots. `raw_capture_logger.py` validates the 8000-record binary capture format, metadata and CRC before writing outputs.

Run `python raw_capture_logger.py --self-test` for standard-library protocol tests. Run `python raw_capture_logger.py --help` for simulation and optional serial capture. Only live serial mode requires pyserial. Run the MATLAB filter script for its numerical checks and plots.

Run `python verify_filters.py` for an independent standard-library check of float32 coefficient stability, cutoff responses and the 50 Hz notch. This does not execute the MATLAB scripts.

The nominal 20 Hz high-pass, 500 Hz low-pass and 50 Hz notch parameters describe software coefficients. This publication does not establish hardware timing, electrode calibration, actual acquisition quality or clinical validity. The interactive MATLAB GUI and live hardware route require separate verification.

Source prepared with AI coding assistance. Capture data, thesis documents and screenshots remain excluded. See VERIFICATION.md for checks performed on this source snapshot.
