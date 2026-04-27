classdef Saver
    methods (Static)
        function save(EEG, cfg)
            filename = [cfg.filename '_preprocessed.set'];
            filepath = fullfile(cfg.path_out, filename);
            pop_saveset(EEG, 'filename', filename, 'filepath', cfg.path_out);
            fprintf('Saved to %s\n', filepath);
        end
    end
end