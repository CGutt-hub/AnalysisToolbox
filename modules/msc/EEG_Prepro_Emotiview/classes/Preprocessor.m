classdef Preprocessor
    methods (Static)
        function EEG = resample(EEG, rate)
            EEG = pop_resample(EEG, rate);
            EEG = eeg_checkset(EEG);
        end

        function EEG = rereference(EEG)
            EEG = pop_reref(EEG, []);
            EEG = eeg_checkset(EEG);
        end

        function EEG = bandpassFilter(EEG, low, high)
            EEG = pop_eegfiltnew(EEG, 'locutoff', low, 'hicutoff', high);
            EEG = eeg_checkset(EEG);
        end
    end
end