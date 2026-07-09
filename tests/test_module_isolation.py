"""
Test that modules are isolated and don't reference services from other modules.

This ensures module decoupling - modules should not hardcode references to specific
services from other modules. Dependencies should be expressed through contracts,
not service names.
"""

import unittest
import yaml
from pathlib import Path


class ModuleIsolationTest(unittest.TestCase):
    """Verify that modules don't cross-reference each other's service names."""

    @classmethod
    def setUpClass(cls):
        """Load all modules and extract their service names."""
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.modules_dir = cls.repo_root / "modules"
        
        # Find all module.yaml files and extract service names
        cls.modules = {}  # {module_name: {service_name, module_path, full_content}}
        cls.service_names = set()  # All service names across all modules
        
        for module_yaml in cls.modules_dir.rglob("module.yaml"):
            try:
                with open(module_yaml, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                
                if content and "metadata" in content and "name" in content["metadata"]:
                    module_name = content["metadata"]["name"]
                    service_name = None
                    
                    # Extract service name from runtime.service.name
                    if "spec" in content and "runtime" in content["spec"]:
                        runtime = content["spec"]["runtime"]
                        if isinstance(runtime, dict) and "service" in runtime:
                            service_config = runtime["service"]
                            if isinstance(service_config, dict) and "name" in service_config:
                                service_name = service_config["name"]
                    
                    if service_name:
                        cls.modules[module_name] = {
                            "service_name": service_name,
                            "module_path": module_yaml,
                            "content": content,
                            "content_str": f.read(),  # Will be re-read below
                        }
                        cls.service_names.add(service_name)
            except Exception as e:
                # Skip modules that can't be parsed
                pass
        
        # Re-read for string content
        for module_name, module_info in cls.modules.items():
            with open(module_info["module_path"], "r", encoding="utf-8") as f:
                module_info["content_str"] = f.read()

    def test_no_cross_module_service_references(self):
        """Verify no module references another module's service name in its config."""
        violations = []
        
        for module_name, module_info in self.modules.items():
            service_name = module_info["service_name"]
            content = module_info["content"]
            
            # Check for references to other service names in this module's config
            for other_service_name in self.service_names:
                if other_service_name == service_name:
                    # Skip self-references
                    continue
                
                # Convert both to lowercase for case-insensitive search in YAML content
                if other_service_name.lower() in module_info["content_str"].lower():
                    # This could be a false positive, so let's verify it's in actual config
                    # by checking specific fields where service references would be problematic
                    config = content.get("spec", {}).get("configSchema", {})
                    config_str = yaml.dump(config).lower()
                    
                    if other_service_name.lower() in config_str:
                        violations.append(
                            f"Module '{module_name}' (service: {service_name}) "
                            f"references another module's service '{other_service_name}' "
                            f"in configSchema"
                        )
                    
                    # Check environment variables and defaults
                    properties = config.get("properties", {})
                    for prop_name, prop_config in properties.items():
                        if isinstance(prop_config, dict):
                            prop_str = yaml.dump(prop_config).lower()
                            if other_service_name.lower() in prop_str:
                                violations.append(
                                    f"Module '{module_name}' (service: {service_name}) "
                                    f"references service '{other_service_name}' "
                                    f"in property '{prop_name}'"
                                )
        
        self.assertEqual(
            len(violations),
            0,
            f"Found {len(violations)} cross-module service reference(s):\n" 
            + "\n".join(violations)
        )

    def test_all_modules_have_service_names(self):
        """Verify that all modules define a service name in runtime.service.name."""
        missing_service_name = []
        
        for module_yaml in self.modules_dir.rglob("module.yaml"):
            try:
                with open(module_yaml, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                
                has_service_name = False
                if content and "spec" in content and "runtime" in content["spec"]:
                    runtime = content["spec"]["runtime"]
                    if (isinstance(runtime, dict) and "service" in runtime and 
                        isinstance(runtime["service"], dict) and 
                        "name" in runtime["service"]):
                        has_service_name = True
                
                if not has_service_name:
                    module_name = (
                        content.get("metadata", {}).get("name") 
                        if content else "unknown"
                    )
                    missing_service_name.append(f"{module_yaml}: module '{module_name}'")
            except Exception:
                pass
        
        self.assertEqual(
            len(missing_service_name),
            0,
            f"Found {len(missing_service_name)} module(s) missing service name definition:\n"
            + "\n".join(missing_service_name)
        )

    def test_module_service_names_are_unique(self):
        """Verify that each module has a unique service name."""
        service_names_to_modules = {}
        
        for module_name, module_info in self.modules.items():
            service_name = module_info["service_name"]
            if service_name in service_names_to_modules:
                service_names_to_modules[service_name].append(module_name)
            else:
                service_names_to_modules[service_name] = [module_name]
        
        duplicates = {
            service_name: modules 
            for service_name, modules in service_names_to_modules.items() 
            if len(modules) > 1
        }
        
        self.assertEqual(
            len(duplicates),
            0,
            f"Found duplicate service names: {duplicates}"
        )


if __name__ == "__main__":
    unittest.main()
