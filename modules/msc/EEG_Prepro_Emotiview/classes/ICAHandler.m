classdef ICAHandler
    methods (Static)
        function [EEG, EEG_ica] = runICA(EEG)
            EEG_ica = pop_eegfiltnew(EEG, 'locutoff', 1.5);
            numComp = min(size(EEG_ica.data, 1), EEG_ica.nbchan);
            EEG_ica = pop_runica(EEG_ica, 'extended', 1, 'pca', numComp);

            EEG.icaweights = EEG_ica.icaweights;
            EEG.icasphere = EEG_ica.icasphere;
            EEG.icawinv = EEG_ica.icawinv;
            EEG.icachansind = EEG_ica.icachansind;
            EEG = eeg_checkset(EEG);
        end

        function EEG = classifyAndReject(EEG)
            EEG = pop_iclabel(EEG, 'default');
            probs = EEG.etc.ic_classification.ICLabel.classifications;
            rej = unique([find(probs(:,2)>=0.8); find(probs(:,3)>=0.8)]);
            if ~isempty(rej)
                EEG = pop_subcomp(EEG, rej, 0);
                EEG.etc.rejected_ica_components = rej;
                EEG = eeg_checkset(EEG);
            end
        end
    end
end