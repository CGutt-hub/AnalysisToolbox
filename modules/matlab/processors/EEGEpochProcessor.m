classdef EEGEpochProcessor
    methods (Static)
        function EEG = epochAndBaseline(EEG, varargin)
            % epochAndBaseline - Epochs data around specified markers and applies baseline correction.
            %
            % Syntax: EEG = EEGEpochProcessor.epochAndBaseline(EEG, params)
            %
            % Inputs:
            %    EEG - EEGLAB EEG structure
            %    params - (Optional) Struct with parameters:
            %             params.epochLimits = [-1 2]; % e.g., in seconds
            %             params.baselineLimits = [-200 0]; % e.g., in milliseconds
            %             params.markers = {'1', '2', ...}; % Cell array of marker strings
            %
            % Outputs:
            %    EEG - Epoched EEGLAB EEG structure

            defaultEpochLimits = [-1 2];
            defaultBaselineLimits = [-200 0];

            allEvents = {EEG.event.type};
            defaultMarkers = unique(cellfun(@num2str, allEvents, 'UniformOutput', false));
            if isempty(defaultMarkers), error('EEGEpochProcessor: No event markers found in EEG.event.type.'); end

            p = inputParser;
            addParameter(p, 'epochLimits', defaultEpochLimits, @(x) isnumeric(x) && numel(x)==2);
            addParameter(p, 'baselineLimits', defaultBaselineLimits, @(x) isnumeric(x) && numel(x)==2);
            addParameter(p, 'markers', defaultMarkers, @(x) iscellstr(x) || ischar(x) || isstring(x));
            parse(p, varargin{:});
            
            params = p.Results;
            
            if ischar(params.markers) && size(params.markers,1) > 1, params.markers = cellstr(params.markers); elseif isstring(params.markers), params.markers = cellstr(char(params.markers)); elseif ischar(params.markers), params.markers = {params.markers}; end

            fprintf('EEGEpochProcessor: Epoching around markers: %s, Limits: [%g %g]s, Baseline: [%g %g]ms\n', strjoin(params.markers, ', '), params.epochLimits(1), params.epochLimits(2), params.baselineLimits(1), params.baselineLimits(2));
            EEG = pop_epoch(EEG, params.markers, params.epochLimits, 'epochinfo', 'yes');
            EEG = pop_rmbase(EEG, params.baselineLimits);
            EEG = eeg_checkset(EEG);
        end
    end
end