classdef Plotter
    methods (Static)
        function saveICATopoplot(EEG, cfg)
            numToPlot = min(36, size(EEG.icawinv, 2));
            if numToPlot > 0
                pop_topoplot(EEG, 0, 1:numToPlot, 'ICA Topographies', [6 6], 0, 'electrodes','off');
                saveas(gcf, [cfg.path_plot 'iclabel_topo_' cfg.filename '.png']);
                close(gcf);
            end
        end
    end
end
