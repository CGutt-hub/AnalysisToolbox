import os
from src.pipeline import PreProcessingPipeline

if __name__ == '__main__':
    PATH = 'D:/Bens Dateien/Uni/FH/8. Semester/PA1/EmotiView/'
    PATH_IN = os.path.join(PATH, 'data/raw/')
    PATH_OUT = os.path.join(PATH, 'data/pre_python/')
    PATH_STAT = os.path.join(PATH, 'stats/')
    PATH_PLOT = os.path.join(PATH, 'plots/')

    pipeline = PreProcessingPipeline(PATH_IN, PATH_OUT, PATH_STAT, PATH_PLOT)
    pipeline.run_pipeline()