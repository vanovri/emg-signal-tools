function filter_response_live
% FILTER_RESPONSE_LIVE Visualisasi interaktif respons filter EMG.
% Geser slider untuk melihat perubahan respons secara langsung.

fs = 4000;
nyquist = fs/2;

% Tutup hanya jendela buatan fungsi ini jika dijalankan ulang.
oldFig = findall(groot,"Type","figure","Tag","EMGFilterLive");
if ~isempty(oldFig)
    delete(oldFig);
end

% Palet gelap yang nyaman untuk demonstrasi di layar.
bg       = [0.055 0.071 0.102];
panel    = [0.090 0.114 0.153];
fg       = [0.930 0.950 0.975];
muted    = [0.650 0.700 0.770];
gridCol  = [0.28 0.32 0.38];
blue     = [0.20 0.62 1.00];
orange   = [1.00 0.55 0.18];
green    = [0.30 0.82 0.54];
white    = [0.96 0.97 0.99];
accent   = [0.34 0.75 1.00];

fig = uifigure( ...
    "Name","Live EMG Digital Filter Explorer", ...
    "Tag","EMGFilterLive", ...
    "Color",bg, ...
    "Position",[40 40 1240 700]);

root = uigridlayout(fig,[3 2]);
root.RowHeight = {58,"1x",36};
root.ColumnWidth = {285,"1x"};
root.Padding = [16 12 16 10];
root.RowSpacing = 10;
root.ColumnSpacing = 14;
root.BackgroundColor = bg;

titleLabel = uilabel(root, ...
    "Text","LIVE EMG DIGITAL FILTER EXPLORER", ...
    "FontName","Segoe UI", ...
    "FontSize",22, ...
    "FontWeight","bold", ...
    "FontColor",fg);
titleLabel.Layout.Row = 1;
titleLabel.Layout.Column = [1 2];

controls = uipanel(root,"BackgroundColor",panel,"BorderType","none");
controls.Layout.Row = 2;
controls.Layout.Column = 1;

cg = uigridlayout(controls,[15 1]);
cg.RowHeight = {30,22,42,22,42,22,42,22,42,18,28,28,34,34,"1x"};
cg.Padding = [16 14 16 12];
cg.RowSpacing = 5;
cg.BackgroundColor = panel;

controlTitle = uilabel(cg,"Text","Parameter penyaring", ...
    "FontSize",17,"FontWeight","bold","FontColor",fg);

hpfText = uilabel(cg,"FontColor",fg,"FontWeight","bold");
hpfSlider = uislider(cg,"Limits",[5 100],"Value",20, ...
    "MajorTicks",[5 20 50 100],"FontColor",muted);

lpfText = uilabel(cg,"FontColor",fg,"FontWeight","bold");
lpfSlider = uislider(cg,"Limits",[100 1000],"Value",500, ...
    "MajorTicks",[100 250 500 750 1000],"FontColor",muted);

notchText = uilabel(cg,"FontColor",fg,"FontWeight","bold");
notchSlider = uislider(cg,"Limits",[45 55],"Value",50, ...
    "MajorTicks",45:2.5:55,"FontColor",muted);

qText = uilabel(cg,"FontColor",fg,"FontWeight","bold");
qSlider = uislider(cg,"Limits",[5 80],"Value",30, ...
    "MajorTicks",[5 20 40 60 80],"FontColor",muted);

uilabel(cg,"Text","Tampilkan respons", ...
    "FontColor",muted,"FontAngle","italic");
showHPF = uicheckbox(cg,"Text","HPF  |  biru", ...
    "Value",true,"FontColor",fg);
showLPF = uicheckbox(cg,"Text","LPF  |  jingga", ...
    "Value",true,"FontColor",fg);
showNotch = uicheckbox(cg,"Text","Notch  |  hijau", ...
    "Value",true,"FontColor",fg);

buttonGrid = uigridlayout(cg,[1 2]);
buttonGrid.ColumnWidth = {"1x","1x"};
buttonGrid.Padding = [0 0 0 0];
buttonGrid.ColumnSpacing = 8;
buttonGrid.BackgroundColor = panel;
resetButton = uibutton(buttonGrid,"Text","Reset", ...
    "BackgroundColor",[0.18 0.22 0.28],"FontColor",fg);
exportButton = uibutton(buttonGrid,"Text","Ekspor PNG", ...
    "BackgroundColor",accent,"FontColor",[0.02 0.07 0.11], ...
    "FontWeight","bold");

statusLabel = uilabel(cg,"Text","", ...
    "FontColor",green,"VerticalAlignment","top","WordWrap","on");

plotPanel = uipanel(root,"BackgroundColor",bg,"BorderType","none");
plotPanel.Layout.Row = 2;
plotPanel.Layout.Column = 2;
pg = uigridlayout(plotPanel,[2 1]);
pg.RowHeight = {"2x","1x"};
pg.Padding = [0 0 0 0];
pg.RowSpacing = 12;
pg.BackgroundColor = bg;

axMain = uiaxes(pg,"Color",[0.030 0.045 0.070],"FontName","Segoe UI", ...
    "FontSize",11,"XColor",fg,"YColor",fg,"GridColor",gridCol, ...
    "MinorGridColor",gridCol);
axMain.Layout.Row = 1;
axMain.XScale = "log";
axMain.XLim = [1 nyquist];
axMain.YLim = [-100 5];
axMain.XTick = [1 5 10 20 50 100 200 500 1000 2000];
axMain.XTickLabel = string(axMain.XTick);
axMain.XGrid = "on";
axMain.YGrid = "on";
axMain.XMinorGrid = "on";
title(axMain,"Respons magnitudo filter dan kaskade total", ...
    "Color",fg,"FontSize",15);
xlabel(axMain,"Frekuensi (Hz)","Color",fg);
ylabel(axMain,"Magnitudo (dB)","Color",fg);
hold(axMain,"on");

axZoom = uiaxes(pg,"Color",[0.030 0.045 0.070],"FontName","Segoe UI", ...
    "FontSize",11,"XColor",fg,"YColor",fg,"GridColor",gridCol);
axZoom.Layout.Row = 2;
axZoom.XLim = [40 60];
axZoom.YLim = [-100 5];
axZoom.XGrid = "on";
axZoom.YGrid = "on";
title(axZoom,"Pembesaran area gangguan listrik 50 Hz", ...
    "Color",fg,"FontSize",13);
xlabel(axZoom,"Frekuensi (Hz)","Color",fg);
ylabel(axZoom,"Magnitudo (dB)","Color",fg);
hold(axZoom,"on");

fBase = unique([logspace(0,log10(nyquist),5000),linspace(40,60,2501)]);

lineHPF = plot(axMain,fBase,nan(size(fBase)),"LineWidth",1.6,"Color",blue);
lineLPF = plot(axMain,fBase,nan(size(fBase)),"LineWidth",1.6,"Color",orange);
lineNotch = plot(axMain,fBase,nan(size(fBase)),"LineWidth",1.6,"Color",green);
lineTotal = plot(axMain,fBase,nan(size(fBase)),"LineWidth",3.0,"Color",white);
yline(axMain,-3,":","-3 dB","Color",muted,"LabelColor",muted);

lineZoomNotch = plot(axZoom,fBase,nan(size(fBase)),"LineWidth",1.8,"Color",green);
lineZoomTotal = plot(axZoom,fBase,nan(size(fBase)),"LineWidth",3.0,"Color",white);
yline(axZoom,-3,":","Color",muted);

legend(axMain,[lineHPF lineLPF lineNotch lineTotal], ...
    {"HPF","LPF","Notch","Kaskade total"}, ...
    "Location","southwest","TextColor",fg,"Color",panel, ...
    "EdgeColor",gridCol);

footer = uilabel(root, ...
    "Text","Geser slider — respons dihitung ulang secara langsung dengan presisi float32 seperti implementasi STM32.", ...
    "FontColor",muted,"FontAngle","italic","HorizontalAlignment","center");
footer.Layout.Row = 3;
footer.Layout.Column = [1 2];

allSliders = [hpfSlider,lpfSlider,notchSlider,qSlider];
for k = 1:numel(allSliders)
    allSliders(k).ValueChangingFcn = @(src,event) liveChange(src,event.Value);
    allSliders(k).ValueChangedFcn = @(~,~) updateResponse();
end
showHPF.ValueChangedFcn = @(~,~) updateVisibility();
showLPF.ValueChangedFcn = @(~,~) updateVisibility();
showNotch.ValueChangedFcn = @(~,~) updateVisibility();
resetButton.ButtonPushedFcn = @(~,~) resetParameters();
exportButton.ButtonPushedFcn = @(~,~) exportSnapshot();

updateResponse();

    function liveChange(src,value)
        src.Value = value;
        updateResponse();
        drawnow limitrate;
    end

    function updateResponse()
        fcHigh = min(hpfSlider.Value,lpfSlider.Value-10);
        fcLow = max(lpfSlider.Value,fcHigh+10);
        fNotch = notchSlider.Value;
        qValue = qSlider.Value;
        f = unique([fBase,fcHigh,fcLow,fNotch]);
        w = 2*pi*f/fs;

        % Desain ulang filter, kemudian bulatkan ke float32 untuk meniru MCU.
        [bh,ah] = butter(2,fcHigh/nyquist,"high");
        [bl,al] = butter(2,fcLow/nyquist,"low");
        [bn,an] = iirnotch(fNotch/nyquist,(fNotch/nyquist)/qValue);
        bh = double(single(bh)); ah = double(single(ah));
        bl = double(single(bl)); al = double(single(al));
        bn = double(single(bn)); an = double(single(an));

        hh = freqz(bh,ah,w);
        hl = freqz(bl,al,w);
        hn = freqz(bn,an,w);
        ht = hh.*hl.*hn;

        dbh = clampDB(hh);
        dbl = clampDB(hl);
        dbn = clampDB(hn);
        dbt = clampDB(ht);

        lineHPF.XData = f;       lineHPF.YData = dbh;
        lineLPF.XData = f;       lineLPF.YData = dbl;
        lineNotch.XData = f;     lineNotch.YData = dbn;
        lineTotal.XData = f;     lineTotal.YData = dbt;
        lineZoomNotch.XData = f; lineZoomNotch.YData = dbn;
        lineZoomTotal.XData = f; lineZoomTotal.YData = dbt;

        hpfText.Text = sprintf("HPF cutoff       %5.1f Hz",fcHigh);
        lpfText.Text = sprintf("LPF cutoff       %5.1f Hz",fcLow);
        notchText.Text = sprintf("Notch center    %5.2f Hz",fNotch);
        qText.Text = sprintf("Notch Q          %5.1f",qValue);

        notchDB = exactMagnitude(bh,ah,bl,al,bn,an,fNotch);
        statusLabel.Text = sprintf(["Kaskade aktif: %.1f–%.1f Hz\n" ...
            "Pelemahan pada %.2f Hz: %.1f dB"], ...
            fcHigh,fcLow,fNotch,notchDB);
        updateVisibility();
    end

    function values = clampDB(h)
        values = max(20*log10(max(abs(h),1e-12)),-100);
    end

    function value = exactMagnitude(bh,ah,bl,al,bn,an,freq)
        ww = 2*pi*freq/fs;
        hh0 = freqz(bh,ah,ww);
        hl0 = freqz(bl,al,ww);
        hn0 = freqz(bn,an,ww);
        value = max(20*log10(max(abs(hh0.*hl0.*hn0),1e-12)),-100);
    end

    function updateVisibility()
        lineHPF.Visible = onOff(showHPF.Value);
        lineLPF.Visible = onOff(showLPF.Value);
        lineNotch.Visible = onOff(showNotch.Value);
        lineZoomNotch.Visible = onOff(showNotch.Value);
    end

    function textValue = onOff(tf)
        if tf
            textValue = "on";
        else
            textValue = "off";
        end
    end

    function resetParameters()
        hpfSlider.Value = 20;
        lpfSlider.Value = 500;
        notchSlider.Value = 50;
        qSlider.Value = 30;
        showHPF.Value = true;
        showLPF.Value = true;
        showNotch.Value = true;
        updateResponse();
    end

    function exportSnapshot()
        outDir = fullfile(fileparts(mfilename("fullpath")),"hasil_respons_filter");
        if ~exist(outDir,"dir")
            mkdir(outDir);
        end
        outFile = fullfile(outDir,"respons_filter_live_snapshot.png");
        exportapp(fig,outFile);
        statusLabel.Text = "Snapshot tersimpan: respons_filter_live_snapshot.png";
    end
end
