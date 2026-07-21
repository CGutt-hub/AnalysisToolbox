"""
Module Model - Models for ATBX module discovery and registry
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from enum import Enum
from pathlib import Path
import os
import re


class ModuleCategory(Enum):
    """Category of ATBX modules."""
    READERS = "Readers"
    PROCESSORS = "Processors"
    ANALYZERS = "Analyzers"
    UTILS = "Utils"
    OTHER = "Other"


class ModuleType(Enum):
    """Type of module."""
    READER = "reader"
    PROCESSOR = "processor"
    ANALYZER = "analyzer"
    UTILITY = "utility"


@dataclass
class ModuleInfo:
    """Information about an ATBX module."""
    id: str
    name: str
    script_path: str
    description: str = ""
    module_type: ModuleType = ModuleType.PROCESSOR
    category: str = "Other"
    has_parameters: bool = False
    parameters: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            **asdict(self),
            'module_type': self.module_type.value
        }


class ModuleRegistry:
    """Registry of all discovered ATBX modules."""
    
    def __init__(self):
        self.modules: Dict[str, ModuleInfo] = {}
        self.categories: Dict[str, List[ModuleInfo]] = {}
        self.types: Dict[str, List[ModuleInfo]] = {}
    
    def add_module(self, module_info: ModuleInfo):
        """Add a module to the registry."""
        self.modules[module_info.id] = module_info
        
        # Add to category
        if module_info.category not in self.categories:
            self.categories[module_info.category] = []
        self.categories[module_info.category].append(module_info)
        
        # Add to type
        type_key = module_info.module_type.value
        if type_key not in self.types:
            self.types[type_key] = []
        self.types[type_key].append(module_info)
    
    def get_modules_by_category(self, category: str) -> List[ModuleInfo]:
        """Get modules in a specific category."""
        return self.categories.get(category, [])
    
    def get_modules_by_type(self, module_type: ModuleType) -> List[ModuleInfo]:
        """Get modules of a specific type."""
        return self.types.get(module_type.value, [])
    
    def search(self, query: str) -> List[ModuleInfo]:
        """Search modules by name or description."""
        query_lower = query.lower()
        return [
            module for module in self.modules.values()
            if (query_lower in module.name.lower() or 
                query_lower in module.description.lower())
        ]


def _determine_category(path: Path) -> str:
    """Determine module category from its path."""
    path_str = str(path).lower()
    
    if any(part in path_str for part in ['readers', 'reader', 'ingest']):
        return ModuleCategory.READERS.value
    elif any(part in path_str for part in ['processors', 'processor', 'filter', 'epoch', 'transform']):
        return ModuleCategory.PROCESSORS.value
    elif any(part in path_str for part in ['analyzers', 'analyzer', 'stats', 'statistical', 'analysis']):
        return ModuleCategory.ANALYZERS.value
    elif any(part in path_str for part in ['utils', 'utility', 'helper']):
        return ModuleCategory.UTILS.value
    else:
        return ModuleCategory.OTHER.value


def _determine_type(filename: str) -> ModuleType:
    """Determine module type from filename."""
    filename_lower = filename.lower()
    
    if any(part in filename_lower for part in ['reader', 'ingest', 'load']):
        return ModuleType.READER
    elif any(part in filename_lower for part in ['analyzer', 'stats', 'statistical', 'anova', 'correlation']):
        return ModuleType.ANALYZER
    elif any(part in filename_lower for part in ['processor', 'filter', 'epoch', 'transform', 'concatenate', 'join']):
        return ModuleType.PROCESSOR
    else:
        return ModuleType.UTILITY


def _extract_description(filepath: Path, filename: str) -> str:
    """Extract module description from file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Look for docstring
        docstring_match = re.search(r'""".*?"""', content, re.DOTALL)
        if docstring_match:
            return docstring_match.group(0).strip('"""').strip()
        
        # Look for first comment
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('#') or line.startswith('"""') or line.startswith("'''"):
                continue
            if line and not line.startswith(('import', 'from', '@')):
                return line[:100]  # First line of code as fallback
                
    except Exception:
        pass
    
    return filename.replace('_', ' ').replace('.py', '').title()


def _scan_modules(directory: Path) -> List[ModuleInfo]:
    """Scan a directory for Python modules."""
    modules = []
    
    if not directory.exists():
        return modules
    
    for filepath in directory.rglob('*.py'):
        # Skip __init__.py and hidden files
        if filepath.name.startswith('_'):
            continue
            
        filename = filepath.name
        
        # Extract module ID (filename without extension)
        module_id = f"{directory.stem}.{filename[:-3]}"  # Remove .py
        
        # Extract name
        name = filename[:-3].replace('_', ' ').title()
        
        # Determine category and type
        category = _determine_category(filepath)
        module_type = _determine_type(filename)
        
        # Extract description
        description = _extract_description(filepath, filename)
        
        # Create module info
        module_info = ModuleInfo(
            id=module_id,
            name=name,
            script_path=str(filepath),
            description=description,
            module_type=module_type,
            category=category
        )
        
        modules.append(module_info)
    
    return modules


def create_module_registry(atbx_path: str) -> ModuleRegistry:
    """Create a module registry by scanning the ATBX directories."""
    registry = ModuleRegistry()
    
    base_path = Path(atbx_path)
    
    # Define scan directories based on ATBX structure
    scan_dirs = [
        base_path / 'gitatbx' / 'modules' / 'readers',
        base_path / 'gitatbx' / 'modules' / 'processors',
        base_path / 'gitatbx' / 'modules' / 'analyzers',
        base_path / 'gitatbx' / 'modules' / 'utils',
        base_path / 'gitatbx' / 'modules',
        base_path / 'gitatbx' / 'bin'
    ]
    
    for scan_dir in scan_dirs:
        if scan_dir.exists():
            modules = _scan_modules(scan_dir)
            for module in modules:
                # Fix category based on directory
                if 'readers' in str(scan_dir):
                    module.category = ModuleCategory.READERS.value
                    module.module_type = ModuleType.READER
                elif 'processors' in str(scan_dir):
                    module.category = ModuleCategory.PROCESSORS.value
                    module.module_type = ModuleType.PROCESSOR
                elif 'analyzers' in str(scan_dir):
                    module.category = ModuleCategory.ANALYZERS.value
                    module.module_type = ModuleType.ANALYZER
                elif 'utils' in str(scan_dir):
                    module.category = ModuleCategory.UTILS.value
                    module.module_type = ModuleType.UTILITY
                
                registry.add_module(module)
    
    return registry