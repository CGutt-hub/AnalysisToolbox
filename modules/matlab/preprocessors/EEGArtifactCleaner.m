classdef EEGArtifactCleaner
    methods (Static)
        function EEG = cleanBadChannels(EEG, varargin)
            % cleanBadChannels - Cleans bad channels using EEGLAB's clean_artifacts.
            %
            % Syntax: EEG = EEGArtifactCleaner.cleanBadChannels(EEG, params)
            %
            % Inputs:
            %    EEG - EEGLAB EEG structure
            %    params - (Optional) Struct with parameters for clean_artifacts.
            %             Example: params.FlatlineCriterion = 5;
            %                      params.ChannelCriterion = 0.8;
            %
            % Outputs:
            %    EEG - EEGLAB EEG structure with cleaned channels

            defaultParams = {'FlatlineCriterion', 5, 'Highpass','off', 'ChannelCriterion', 0.85, 'LineNoiseCriterion', 4, 'BurstCriterion', 20, 'WindowCriterion','off'};
            paramsIn = defaultParams; 
            if nargin > 1 && isstruct(varargin{1})
                userParams = varargin{1};
                fields = fieldnames(userParams);
                for i = 1:length(fields)
                    idx = find(strcmpi(defaultParams, fields{i}));
                    if ~isempty(idx)
                        paramsIn{idx+1} = userParams.(fields{i});
                    else
                        paramsIn{end+1} = fields{i};
                        paramsIn{end+1} = userParams.(fields{i});
                    end
                end
            end
            fprintf('EEGArtifactCleaner: Applying clean_artifacts with parameters: %s\n', strjoin(cellfun(@(x) num2str(x), paramsIn, 'UniformOutput', false), ', '));
            EEG = clean_artifacts(EEG, paramsIn{:});
            EEG = eeg_checkset(EEG);
        end
    end
end