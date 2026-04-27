classdef EEGLABSaver
    methods (Static)
        function saveSet(EEG, varargin)
            % saveSet - Saves an EEGLAB EEG dataset.
            %
            % Syntax: EEGLABSaver.saveSet(EEG, params)
            %         EEGLABSaver.saveSet(EEG, 'filename', 'mydata.set', 'filepath', '/path/to/save')
            %
            % Inputs:
            %    EEG - EEGLAB EEG structure
            %    params - (Optional) Struct with parameters for pop_saveset:
            %             params.filename = 'mydata.set';
            %             params.filepath = '/path/to/save/';
            %             params.savemode = 'onefile'; % or 'twofiles'
            %             params.version = '7.3'; % for MAT files > 2GB
            %    Alternatively, pass key-value pairs for pop_saveset.
            %
            % Outputs:
            %    None

            p = inputParser;
            addParameter(p, 'filename', [EEG.setname, '.set'], @ischar);
            addParameter(p, 'filepath', pwd, @ischar); % Default to current directory
            addParameter(p, 'savemode', 'onefile', @(x) ismember(x, {'onefile', 'twofiles'}));
            addParameter(p, 'version', '7.3', @ischar); % For MAT files > 2GB
            
            % Allow passing a struct or key-value pairs
            if nargin > 1 && isstruct(varargin{1})
                structParams = varargin{1};
                fn = fieldnames(structParams);
                for k = 1:numel(fn)
                    % Check if the field is a valid parameter for inputParser
                    if ismember(fn{k}, p.Parameters)
                        % Directly pass to parse, inputParser handles it
                        varargin = [varargin, {fn{k}, structParams.(fn{k})}];
                    else
                        % If not a defined parameter, it might be a direct pop_saveset option
                        % This part is tricky with inputParser; simpler to just pass all varargin
                        % to pop_saveset if not using inputParser strictly for all options.
                        % For now, we'll stick to defined parameters.
                        fprintf('EEGLABSaver: Ignoring unknown parameter in struct: %s\n', fn{k});
                    end
                end
                % Re-parse with potentially added key-value pairs from struct
                % This logic is a bit complex if mixing struct and direct varargin.
                % Simpler: if struct, use its fields. If varargin, use that.
                % For now, let's assume varargin are key-value pairs directly.
            end

            parse(p, varargin{:});
            params = p.Results;

            if ~exist(params.filepath, 'dir')
                mkdir(params.filepath);
                fprintf('EEGLABSaver: Created directory %s\n', params.filepath);
            end

            fprintf('EEGLABSaver: Saving dataset %s to %s\n', params.filename, params.filepath);
            pop_saveset(EEG, 'filename', params.filename, 'filepath', params.filepath, ...
                        'savemode', params.savemode, 'version', params.version);
        end
    end
end