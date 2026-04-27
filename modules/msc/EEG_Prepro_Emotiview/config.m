function cfg = config()
    base = 'D:/Bens Dateien/Uni/FH/EmotiView/';
    cfg.path_in = [base 'data/raw/'];
    cfg.path_out = [base 'data/preprocessed/'];
    cfg.path_plot = [base 'plots/'];
    cfg.path_stats = [base 'stats/'];
    cfg.filename = 'EV_P005';
    cfg.exclude_channels = {'PB1','PB2','PB3','PB4','PB5','PPG','EDA','EKG','AF4','AF3','P1','P2','P3','P4','PO3','PO4','triggerStream'};
end