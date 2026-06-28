from dagster import Definitions, asset, define_asset_job


@asset
def hello_cds() -> str:
    return "hello from cds"


hello_cds_job = define_asset_job("hello_cds_job", selection=["hello_cds"])


defs = Definitions(assets=[hello_cds], jobs=[hello_cds_job])
