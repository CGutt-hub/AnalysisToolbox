classdef ChannelEditor
    methods (Static)
        function EEG = addLocations(EEG)
            locFile = which('standard-10-5-cap385.elp');
            if isempty(locFile), error('Channel location file not found.'); end
            EEG = pop_chanedit(EEG, 'lookup', locFile);
            EEG = eeg_checkset(EEG);
        end

        function EEG = removeChannels(EEG, excludeList)
            labels = {EEG.chanlocs.labels};
            keepIdx = find(~ismember(labels, excludeList));
            EEG = pop_select(EEG, 'channel', keepIdx);
            EEG = eeg_checkset(EEG);
        end
    end
end