classdef EEGChannelEditor
    methods (Static)
        function EEG = addLocations(EEG, locFilePath, varargin)
            % addLocations - Adds channel locations from a file.
            %
            % Syntax: EEG = EEGChannelEditor.addLocations(EEG, locFilePath, optionalArgs)
            %
            % Inputs:
            %    EEG - EEGLAB EEG structure
            %    locFilePath - Path to the channel location file (e.g., .ced, .sfp)
            %    optionalArgs - Optional arguments for pop_chanedit (e.g., 'convert','chancenter')
            %
            % Outputs:
            %    EEG - EEGLAB EEG structure with channel locations

            if ~exist(locFilePath, 'file')
                error('EEGChannelEditor: Location file not found: %s', locFilePath);
            end
            
            fprintf('EEGChannelEditor: Adding channel locations from %s\n', locFilePath);
            % Basic call, can be expanded with more pop_chanedit options via varargin
            EEG = pop_chanedit(EEG, 'lookup', locFilePath, varargin{:}); 
            EEG = eeg_checkset(EEG);
        end

        function EEG = removeChannels(EEG, excludeList)
            % removeChannels - Removes specified channels from the EEG dataset.
            %
            % Syntax: EEG = EEGChannelEditor.removeChannels(EEG, excludeList)
            %
            % Inputs:
            %    EEG - EEGLAB EEG structure
            %    excludeList - Cell array of channel names or array of channel indices to remove.
            %
            % Outputs:
            %    EEG - EEGLAB EEG structure with specified channels removed.

            if isempty(excludeList)
                fprintf('EEGChannelEditor: No channels specified for removal.\n');
                return;
            end
            fprintf('EEGChannelEditor: Removing channels: %s\n', strjoin(cellfun(@num2str, excludeList, 'UniformOutput', false), ', '));
            EEG = pop_select(EEG, 'rmchannel', excludeList);
            EEG = eeg_checkset(EEG);
        end
    end
end