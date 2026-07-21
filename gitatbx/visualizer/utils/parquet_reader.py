"""
ATBX Pipeline Visualizer - Parquet Log Reader

Reads .log.parquet files created by Nextflow processes.
Pure utility - no AI, no external dependencies beyond polars.
"""

import polars as pl
from pathlib import Path
from typing import Dict, List, Optional, Any, Iterator
from datetime import datetime
from dataclasses import dataclass
from enum import Enum


class NodeStatus(Enum):
    """Status of a pipeline node."""
    INACTIVE = "inactive"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAIL = "fail"


@dataclass
class ProcessLogEntry:
    """A single log entry from a process execution."""
    timestamp: Optional[datetime]
    participant_id: Optional[str]
    process_name: str
    node_id: str
    exit_code: int
    error_message: Optional[str]
    duration_seconds: Optional[float]
    input_files: List[str]
    output_files: List[str]
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ProcessLogEntry':
        """Create from dictionary."""
        try:
            timestamp = datetime.fromisoformat(data.get('timestamp', '1970-01-01T00:00:00'))
        except:
            timestamp = None
            
        return cls(
            timestamp=timestamp,
            participant_id=data.get('participant_id'),
            process_name=data.get('process_name', data.get('process', '')),
            node_id=data.get('node_id', data.get('process_id', data.get('process_name', ''))),
            exit_code=data.get('exit_code', 0),
            error_message=data.get('error_message'),
            duration_seconds=data.get('duration_seconds'),
            input_files=data.get('input_files', []),
            output_files=data.get('output_files', [])
        )


class LogReader:
    """Reads .log.parquet files from Nextflow/ATBX."""
    
    LOG_FILE_PATTERN = "*_log.parquet"
    
    @classmethod
    def find_log_files(cls, directory: str) -> List[Path]:
        """Find all .log.parquet files in directory tree."""
        path = Path(directory)
        log_files = []
        
        if path.exists():
            log_files = list(path.rglob(cls.LOG_FILE_PATTERN))
            common_dirs = [
                path / "EV_results" / "EV2_results" / ".bin",
                path / "EV_results" / "EV1_results" / ".bin",
                path / ".bin",
            ]
            for d in common_dirs:
                if d.exists():
                    log_files.extend(list(d.glob(cls.LOG_FILE_PATTERN)))
        
        return log_files
    
    @classmethod
    def read_all_logs(cls, directory: str) -> pl.DataFrame:
        """Read and concatenate all log files."""
        log_files = cls.find_log_files(directory)
        dfs = []
        
        for log_file in log_files:
            try:
                df = pl.read_parquet(log_file, use_pyarrow=True)
                if not df.is_empty():
                    df = df.with_columns(pl.lit(str(log_file)).alias("source_file"))
                    dfs.append(df)
            except Exception:
                continue
        
        return pl.concat(dfs, how="diagonal") if dfs else pl.DataFrame()
    
    @classmethod
    def get_pipeline_status(cls, directory: str) -> Dict[str, Any]:
        """Get overall pipeline status."""
        df = cls.read_all_logs(directory)
        
        if df.is_empty():
            return {
                'total_processes': 0,
                'success_count': 0,
                'fail_count': 0,
                'participants': [],
                'nodes': {},
                'last_updated': None
            }
        
        participants = df['participant_id'].drop_nulls().unique().to_list()
        
        node_status = {}
        for row in df.iter_rows(named=True):
            node_id = row.get('process_name', row.get('node_id', 'unknown'))
            if node_id not in node_status:
                node_status[node_id] = {
                    'total': 0, 'success': 0, 'fail': 0, 'participants': set()
                }
            
            node_status[node_id]['total'] += 1
            node_status[node_id]['participants'].add(row.get('participant_id', ''))
            
            if row.get('exit_code', 0) != 0:
                node_status[node_id]['fail'] += 1
            else:
                node_status[node_id]['success'] += 1
        
        nodes = {}
        for node_id, stats in node_status.items():
            if stats['fail'] > 0:
                status = 'fail'
            elif stats['success'] < stats['total']:
                status = 'partial'
            else:
                status = 'success'
            
            nodes[node_id] = {
                'status': status,
                'total': stats['total'],
                'success': stats['success'],
                'fail': stats['fail'],
                'participant_count': len([p for p in stats['participants'] if p])
            }
        
        return {
            'total_processes': len(df),
            'success_count': sum(s['success'] for s in node_status.values()),
            'fail_count': sum(s['fail'] for s in node_status.values()),
            'participants': participants,
            'nodes': nodes
        }
    
    @classmethod
    def get_participant_flow(cls, participant_id: str, directory: str) -> List[Dict[str, Any]]:
        """Trace a participant's flow."""
        df = cls.read_all_logs(directory)
        participant_df = df.filter(pl.col('participant_id') == participant_id)
        
        if participant_df.is_empty():
            return []
        
        flow = []
        for row in participant_df.iter_rows(named=True):
            flow.append({
                'process_name': row.get('process_name', ''),
                'node_id': row.get('node_id', ''),
                'status': 'fail' if row.get('exit_code', 0) != 0 else 'success',
                'exit_code': row.get('exit_code', 0),
                'error_message': row.get('error_message'),
                'timestamp': str(row.get('timestamp', '')),
                'input_files': row.get('input_files', []),
                'output_files': row.get('output_files', [])
            })
        
        flow.sort(key=lambda x: x.get('timestamp', '1970-01-01T00:00:00'))
        return flow
