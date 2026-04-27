classdef Epoching
    methods (Static)
        function EEG = epochAndBaseline(EEG)
            allEvents = {EEG.event.type};
            nums = unique(str2double(allEvents));
            nums = nums(~isnan(nums));
            if isempty(nums), error('No numeric markers found.'); end
            markers = cellfun(@num2str, num2cell(nums), 'UniformOutput', false);

            EEG = pop_epoch(EEG, markers, [-1 2], 'epochinfo', 'yes');
            EEG = pop_rmbase(EEG, [-200 0]);
            EEG = eeg_checkset(EEG);
        end
    end
end