"""ANOVA Analyzer - Perform ANOVA on epoched data with optional FDR correction."""
import polars as pl, pingouin as pg, sys, os
import statsmodels.stats.multitest as mt

# Logging helpers
def log_info(msg): print(f"[anova] INFO: {msg}")
def log_warning(msg): print(f"[anova] WARNING: {msg}")
def log_error(msg): print(f"[anova] ERROR: {msg}")

def anova_analyze(ip: str, dv: str, between: str, participant_id: str, apply_fdr: bool = False, y_lim: float | None = None) -> str:
    if not os.path.exists(ip): log_error(f"File not found: {ip}"); sys.exit(1)
    print(f"[anova] ANOVA: {ip}, dv={dv}, between={between}, fdr={apply_fdr}")
    df = pl.read_parquet(ip).to_pandas()
    results = pl.DataFrame(pg.anova(data=df, dv=dv, between=between, detailed=True))
    if apply_fdr:
        rejected, p_fdr = mt.fdrcorrection(results['p-unc'].to_numpy())
        results = results.with_columns([pl.Series("p_fdr", p_fdr), pl.Series("rejected", rejected)])
    results = results.with_columns([
        pl.lit("bar").alias("plot_type"), pl.lit("ordinal").alias("x_scale"), pl.lit("nominal").alias("y_scale"),
        pl.col("Source").alias("x_data"), pl.col("F").alias("y_data"), pl.lit("F-statistic").alias("y_label"),
        pl.lit(y_lim).alias("y_ticks"), pl.lit(1).alias("plot_weight")])
    out_file = f"{os.path.splitext(os.path.basename(ip))[0]}_anova.parquet"
    results.write_parquet(out_file, compression='snappy')
    # Create procedure visualization
    vis_file = out_file.replace('.parquet', '_vis.parquet')
    results.write_parquet(vis_file, compression='snappy')
    print(f"[anova] Output: {out_file}")
    print(f"[anova] Created procedure visualization: {vis_file}")
    return out_file

if __name__ == '__main__': (lambda a: anova_analyze(a[1], a[2], a[3], a[4], len(a) > 5 and a[5].lower() in ['1','true','yes'], float(a[6]) if len(a) > 6 and a[6] else None) if len(a) >= 5 else (print('[anova] Perform ANOVA with optional FDR correction. Plot-ready output.\nUsage: anova_analyzer.py <input.parquet> <dv> <between> <participant_id> [apply_fdr=false] [y_lim]'), sys.exit(1)))(sys.argv)