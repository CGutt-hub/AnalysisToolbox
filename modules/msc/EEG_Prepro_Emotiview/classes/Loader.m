classdef Loader
    methods (Static)
        function EEG = loadXDF(cfg)
            EEG = pop_loadxdf([cfg.path_in cfg.filename '.xdf'], ...
                              'streamtype', 'EEG', ...
                              'exclude_markerstreams', {});
            EEG.setname = [cfg.filename '_raw'];
            EEG = eeg_checkset(EEG);
        end
    end
end
