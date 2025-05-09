# /// script
# dependencies = [
#   "PyYAML",
# ]
# ///

import argparse
import os
import subprocess
from typing import Dict
import yaml
import tempfile

EFR_SHARD_ATTR = "x-shard"


def is_needed(node: Dict, shard: str):
    # We take only a node to a target if it is explicitly labeled as such
    # shards are comma-separated {proxy,app,db}
    # Note: Volume nodes can be empty
    if not node:
        return False
    shards = node.get(EFR_SHARD_ATTR, "")
    return shard in shards.split(",")


def drop_map_refs_to_services(node_with_refs: Dict, services: Dict) -> Dict:
    for service_name in list(node_with_refs.keys()):
        if service_name not in services.keys():
            node_with_refs.pop(service_name)
    return node_with_refs


def drop_list_refs_to_volumes(service_volumes: list, volumes: Dict) -> list:
    for volume_ref in service_volumes:
        if volume_ref["source"] not in volumes.keys():
            service_volumes.remove(volume_ref)
    return service_volumes


class ComposeShard:
    def __init__(self, name):
        self.name = name
        self.services = {}
        self.networks = {}
        self.volumes = {}

    def pick_needed(self, master_node: Dict, target_node: Dict):
        for k, v in master_node.items():
            if is_needed(v, self.name):
                target_node[k] = v

    def filter(self, master_compose: Dict) -> "ComposeShard":
        for key in ["services", "networks", "volumes"]:
            self.pick_needed(master_compose.get(key, {}), getattr(self, key))
        return self

    def fixup_services(self) -> "ComposeShard":
        for service in self.services.values():
            if "depends_on" in service:
                drop_map_refs_to_services(service["depends_on"], self.services)
            if "networks" in service:
                drop_map_refs_to_services(service["networks"], self.networks)
            if "volumes" in service:
                drop_list_refs_to_volumes(service["volumes"], self.volumes)
            if "build" in service:
                service.pop("build", None)
            # Drop empty nodes
            for attr in ["depends_on", "networks", "volumes"]:
                if not service.get(attr):
                    service.pop(attr, None)
        return self

    def drop_annotations(self):
        for nodes in [self.services, self.networks, self.volumes]:
            for node in nodes.values():
                node.pop(EFR_SHARD_ATTR, None)

    def to_dict(self) -> Dict:
        self.drop_annotations()
        d = {}
        if self.name:
            d["name"] = self.name
        for key in ["services", "networks", "volumes"]:
            if getattr(self, key, None):
                d[key] = getattr(self, key)
        return d


def shard_compose(compose: ComposeShard) -> list[Dict]:
    shards = []
    for target in ["proxy", "app", "db"]:
        c = ComposeShard(target)
        c.filter(compose)
        c.fixup_services()
        shards.append(c.to_dict())
    return shards


def load_merged_master(input_file: str, extra_file: str) -> Dict:
    merged_master_compose = None
    with tempfile.NamedTemporaryFile() as tf:
        cmd = r"docker compose -f {} "
        if extra_file:
            cmd += r"-f {} "
        cmd += r"config -o {} --no-interpolate --no-normalize --no-path-resolution"

        if extra_file:
            cmd = cmd.format(input_file, extra_file, tf.name)
        else:
            cmd = cmd.format(input_file, tf.name)

        mergecmd = subprocess.run(cmd, shell=True)
        if mergecmd.returncode != 0:
            print("=> docker compose failed to merge! 🚫")
            exit(1)

        with open(tf.name, "r") as f:
            merged_master_compose = yaml.safe_load(f)

    if not merged_master_compose:
        print("=> docker compose failed to load! 🚫")
        exit(1)
    return merged_master_compose


def dump_shards(shards: list[Dict], args):
    nfails = 0

    if not os.path.exists(args.outdir):
        os.makedirs(args.outdir)

    for filename, shard in zip([args.proxy, args.app, args.db], shards):
        with open(os.path.join(args.outdir, filename), "w") as f:
            yaml.dump(shard, f)
            print(f"Generated {f.name}")
            if args.verify:
                cp = subprocess.run(
                    "docker compose -f {} --env-file {} config -q".format(
                        f.name, args.configs
                    ),
                    shell=True,
                )
                if cp.returncode != 0:
                    print("=> docker compose failed to parse! 🚫")
                    nfails += 1
                else:
                    print("=> docker compose config valid! ✅")
    if args.verify and nfails:
        print(f"{nfails} shards are invalid! 🚫")
        exit(1)


def main():
    parser = argparse.ArgumentParser(description="Shard a Docker Compose file")
    parser.add_argument(
        "-i",
        "--input",
        default="compose.yaml",
        help="Input compose file (default: compose.yaml)",
    )
    parser.add_argument(
        "-e",
        "--extra",
        default="",
        help="Additional overlay compose file (default: none)",
    )
    parser.add_argument(
        "-o",
        "--outdir",
        default=".",
        help="Output directory (default: .)",
    )
    parser.add_argument(
        "-a",
        "--app",
        default="app.compose.yaml",
        help="Output app compose file (default: app.compose.yaml)",
    )
    parser.add_argument(
        "-p",
        "--proxy",
        default="proxy.compose.yaml",
        help="Output proxy compose file (default: proxy.compose.yaml)",
    )
    parser.add_argument(
        "-d",
        "--db",
        default="db.compose.yaml",
        help="Output db compose file (default: db.compose.yaml)",
    )
    parser.add_argument(
        "-v",
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify the shards via `docker compose config` (default: True)",
    )
    parser.add_argument(
        "-c",
        "--configs",
        default=".env",
        help="Config file to use during verification (default: .env)",
    )

    args = parser.parse_args()

    print(f"Splitting {args.input} into {args.proxy}, {args.app}, {args.db}")
    compose = load_merged_master(args.input, args.extra)
    shards = shard_compose(compose)
    dump_shards(shards, args)


if __name__ == "__main__":
    main()
