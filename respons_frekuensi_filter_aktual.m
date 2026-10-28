%% RESPONS FREKUENSI PENYARING DIGITAL sEMG
% Koefisien diambil langsung dari firmware STM32F411CEU6:
% HPF Butterworth orde 2, 20 Hz
% LPF Butterworth orde 2, 500 Hz
% Notch orde 2, 50 Hz, Q = 30
% Frekuensi sampling = 4000 Hz

clear;
clc;
close all;

%% Parameter sistem
fs = 4000;                 % Frekuensi sampling (Hz)
floorDB = -100;            % Batas bawah grafik (dB)
fMin = 1;                  % Batas frekuensi minimum grafik (Hz)
fMax = fs/2;               % Frekuensi Nyquist (Hz)

%% Koefisien aktual firmware (dikuantisasi sebagai float32)
% Bentuk denominator MATLAB: A(z) = 1 + a1*z^-1 + a2*z^-2
bHPF = double(single([ ...
     0.9780304792, -1.9560609584,  0.9780304792]));
aHPF = double(single([ ...
     1.0000000000, -1.9555782403,  0.9565436765]));

bLPF = double(single([ ...
     0.0976310729,  0.1952621459,  0.0976310729]));
aLPF = double(single([ ...
     1.0000000000, -0.9428090416,  0.3333333333]));

bNotch = double(single([ ...
     0.9986927135, -1.9912281544,  0.9986927135]));
aNotch = double(single([ ...
     1.0000000000, -1.9912281544,  0.9973854271]));

%% Pemeriksaan kestabilan
assert(all(abs(roots(aHPF)) < 1),   'HPF tidak stabil.');
assert(all(abs(roots(aLPF)) < 1),   'LPF tidak stabil.');
assert(all(abs(roots(aNotch)) < 1), 'Notch tidak stabil.');

%% Vektor frekuensi
% Titik dibuat rapat di sekitar 50 Hz agar notch terlihat jelas.
f = unique([ ...
    logspace(log10(fMin), log10(fMax), 40000), ...
    linspace(40, 60, 20001), ...
    20, 50, 500]).';
w = 2*pi*f/fs;

%% Respons setiap tahap dan respons kaskade
H_HPF   = freqz(bHPF,   aHPF,   w);
H_LPF   = freqz(bLPF,   aLPF,   w);
H_Notch = freqz(bNotch, aNotch, w);
H_Total = H_HPF .* H_LPF .* H_Notch;

toDB = @(H) 20*log10(max(abs(H), 10^(floorDB/20)));

magHPF   = toDB(H_HPF);
magLPF   = toDB(H_LPF);
magNotch = toDB(H_Notch);
magTotal = toDB(H_Total);

%% Warna grafik
cHPF   = [0.0000, 0.4470, 0.7410];
cLPF   = [0.8500, 0.3250, 0.0980];
cNotch = [0.4660, 0.6740, 0.1880];
cTotal = [0.1000, 0.1000, 0.1000];
cGuide = [0.4500, 0.4500, 0.4500];

%% Pembuatan gambar
fig = figure( ...
    'Color', 'white', ...
    'Units', 'centimeters', ...
    'Position', [2, 2, 16, 16]);

tl = tiledlayout(fig, 2, 1, ...
    'TileSpacing', 'compact', ...
    'Padding', 'compact');

% (a) Respons lengkap
ax1 = nexttile(tl, 1);
semilogx(ax1, f, magHPF,   '-',  'Color', cHPF,   'LineWidth', 1.25);
hold(ax1, 'on');
semilogx(ax1, f, magLPF,   '--', 'Color', cLPF,   'LineWidth', 1.25);
semilogx(ax1, f, magNotch, '-.', 'Color', cNotch, 'LineWidth', 1.25);
semilogx(ax1, f, magTotal, '-',  'Color', cTotal, 'LineWidth', 1.80);

xline(ax1, 20,  ':', 'Color', cGuide);
xline(ax1, 50,  ':', 'Color', cGuide);
xline(ax1, 500, ':', 'Color', cGuide);
yline(ax1, -3, ':', 'Color', cGuide);

xlim(ax1, [fMin, fMax]);
ylim(ax1, [floorDB, 5]);
xticks(ax1, [1, 5, 10, 20, 50, 100, 200, 500, 1000, 2000]);
ylabel(ax1, 'Magnitudo (dB)');
title(ax1, '(a) Respons setiap penyaring dan respons kaskade');
legend(ax1, ...
    {'HPF 20 Hz', 'LPF 500 Hz', 'Notch 50 Hz', 'Kaskade total'}, ...
    'Location', 'southwest', ...
    'NumColumns', 2);
grid(ax1, 'on');
box(ax1, 'on');

% (b) Pembesaran notch 50 Hz
ax2 = nexttile(tl, 2);
zoomMask = (f >= 40) & (f <= 60);
plot(ax2, f(zoomMask), magNotch(zoomMask), '-.', ...
    'Color', cNotch, 'LineWidth', 1.35);
hold(ax2, 'on');
plot(ax2, f(zoomMask), magTotal(zoomMask), '-', ...
    'Color', cTotal, 'LineWidth', 1.80);
xline(ax2, 50, ':', 'Color', cGuide);
yline(ax2, -3, ':', 'Color', cGuide);

xlim(ax2, [40, 60]);
ylim(ax2, [floorDB, 5]);
xticks(ax2, 40:2:60);
xlabel(ax2, 'Frekuensi (Hz)');
ylabel(ax2, 'Magnitudo (dB)');
title(ax2, '(b) Pembesaran respons notch 50 Hz');
legend(ax2, {'Notch 50 Hz', 'Kaskade total'}, 'Location', 'southeast');
grid(ax2, 'on');
box(ax2, 'on');

set([ax1, ax2], ...
    'FontName', 'Times New Roman', ...
    'FontSize', 10, ...
    'LineWidth', 1.0, ...
    'Layer', 'top');

%% Validasi nilai respons pada frekuensi penting
fCheck = [5; 10; 20; 40; 50; 100; 250; 500; 750; 1000];
wCheck = 2*pi*fCheck/fs;

HCheck = ...
    freqz(bHPF, aHPF, wCheck) .* ...
    freqz(bLPF, aLPF, wCheck) .* ...
    freqz(bNotch, aNotch, wCheck);

magCheck = 20*log10(max(abs(HCheck), realmin));
hasilRespons = table( ...
    fCheck, round(magCheck, 3), ...
    'VariableNames', {'Frekuensi_Hz', 'Magnitudo_dB'});

disp('Nilai respons kaskade pada frekuensi penting:');
disp(hasilRespons);

%% Ekspor hasil
scriptFolder = fileparts(mfilename('fullpath'));
if isempty(scriptFolder)
    scriptFolder = pwd;
end

outputFolder = fullfile(scriptFolder, 'hasil_respons_filter');
if ~exist(outputFolder, 'dir')
    mkdir(outputFolder);
end

pngFile = fullfile(outputFolder, 'respons_frekuensi_filter_aktual.png');
pdfFile = fullfile(outputFolder, 'respons_frekuensi_filter_aktual.pdf');
csvFile = fullfile(outputFolder, 'nilai_respons_frekuensi.csv');
matFile = fullfile(outputFolder, 'koefisien_filter_aktual.mat');

exportgraphics(tl, pngFile, ...
    'Resolution', 600, ...
    'BackgroundColor', 'white');

exportgraphics(tl, pdfFile, ...
    'ContentType', 'vector', ...
    'BackgroundColor', 'white');

writetable(hasilRespons, csvFile);
save(matFile, ...
    'fs', ...
    'bHPF', 'aHPF', ...
    'bLPF', 'aLPF', ...
    'bNotch', 'aNotch');

fprintf('\nBerkas berhasil dibuat:\n');
fprintf('PNG : %s\n', pngFile);
fprintf('PDF : %s\n', pdfFile);
fprintf('CSV : %s\n', csvFile);
fprintf('MAT : %s\n', matFile);

% Caption yang disarankan:
% Gambar 3.x Respons magnitudo kaskade HPF Butterworth 20 Hz,
% LPF Butterworth 500 Hz, dan notch 50 Hz berdasarkan koefisien
% implementasi pada STM32F411CEU6 (fs = 4000 Hz).
