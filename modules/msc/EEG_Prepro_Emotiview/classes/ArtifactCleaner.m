classdef ArtifactCleaner
    methods (Static)
        function EEG = cleanBadChannels(EEG)
            EEG = clean_artifacts(EEG, 'FlatlineCriterion', 5, 'Highpass','off', ...
                'ChannelCriterion', 0.85, 'LineNoiseCriterion', 4, ...
                'BurstCriterion', 5, 'WindowCriterion','off');
            EEG = eeg_checkset(EEG);
        end
    end
end
