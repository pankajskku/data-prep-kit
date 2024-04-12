# (C) Copyright IBM Corp. 2024.
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

import kfp.compiler as compiler
import kfp.components as comp
import kfp.dsl as dsl
from kfp_support.workflow_support.utils import (
    ONE_HOUR_SEC,
    ONE_WEEK_SEC,
    ComponentUtils,
)
from kubernetes import client as k8s_client


# the name of the job script
EXEC_SCRIPT_NAME: str = "transformer_launcher.py"

# components
base_kfp_image = "us.icr.io/cil15-shared-registry/preprocessing-pipelines/kfp-data-processing:0.0.3"
# compute execution parameters. Here different tranforms might need different implementations. As
# a result, insted of creating a component we are creating it in place here.
compute_exec_params_op = comp.func_to_container_op(
    func=ComponentUtils.default_compute_execution_params, base_image=base_kfp_image
)
# create Ray cluster
create_ray_op = comp.load_component_from_file("../../../kfp_ray_components/createRayComponent.yaml")
# execute job
execute_ray_jobs_op = comp.load_component_from_file("../../../kfp_ray_components/executeRayJobComponent_multi_s3.yaml")
# clean up Ray
cleanup_ray_op = comp.load_component_from_file("../../../kfp_ray_components/cleanupRayComponent.yaml")
# Task name is part of the pipeline name, the ray cluster name and the job name in DMF.
TASK_NAME: str = "blocklist"
PREFIX: str = "blocklist"


@dsl.pipeline(
    name=TASK_NAME + "-ray-pipeline",
    description="Pipeline for blocklisting",
)
def blocklisting(
    ray_name: str = "blocklisting-kfp-ray",  # name of Ray cluster
    ray_head_options: str = '{"cpu": 1, "memory": 4, "image": "us.icr.io/cil15-shared-registry/preprocessing-pipelines/blocklist:guftest",\
             "image_pull_secret": "prod-all-icr-io"}',  # pragma: allowlist secret
    ray_worker_options: str = '{"replicas": 2, "max_replicas": 2, "min_replicas": 2, "cpu": 2, "memory": 4, \
            "image_pull_secret": "prod-all-icr-io", "image": "us.icr.io/cil15-shared-registry/preprocessing-pipelines/blocklist:guftest"}',  # pragma: allowlist secret
    server_url: str = "http://kuberay-apiserver-service.kuberay.svc.cluster.local:8888",
    additional_params: str = '{"wait_interval": 2, "wait_cluster_ready_tmout": 400, "wait_cluster_up_tmout": 300, "wait_job_ready_tmout": 400, "wait_print_tmout": 30, "http_retries": 5}',
    lh_config: str = "None",
    max_files: int = -1,
    actor_options: str = "{'num_cpus': 0.8}",
    pipeline_id: str = "pipeline_id",
    s3_access_secret: str = "cos-access",
    s3_config: str = "{'input_folder': 'cos-optimal-llm-pile/sanity-test/input/dataset=text/', 'output_folder': 'cos-optimal-llm-pile/doc_annotation_test/output_blocklist_guf/'}",
    blocklist_annotation_column_name: str = "blocklisted",
    blocklist_source_url_column_name: str = "title",
    blocklist_blocked_domain_list_path: str = "cos-optimal-llm-pile/doc_annotation_test/domains",
    blocklist_s3_config="{'input_folder': 'cos-optimal-llm-pile/sanity-test/input/dataset=text/', 'output_folder': 'cos-optimal-llm-pile/doc_annotation_test/output_blocklist_guf/'}",
    blocklist_s3_access_secret: str = "cos-access",
) -> None:
    """
    Pipeline to execute NOOP transform
    :param ray_name: name of the Ray cluster
    :param ray_head_options: head node options, containing the following:
        cpu - number of cpus
        memory - memory
        image - image to use
        image_pull_secret - image pull secret
    :param ray_worker_options: worker node options (we here are using only 1 worker pool), containing the following:
        replicas - number of replicas to create
        max_replicas - max number of replicas
        min_replicas - min number of replicas
        cpu - number of cpus
        memory - memory
        image - image to use
        image_pull_secret - image pull secret
    :param server_url - server url
    :param additional_params: additional (support) parameters, containing the following:
        wait_interval - wait interval for API server, sec
        wait_cluster_ready_tmout - time to wait for cluster ready, sec
        wait_cluster_up_tmout - time to wait for cluster up, sec
        wait_job_ready_tmout - time to wait for job ready, sec
        wait_print_tmout - time between prints, sec
        http_retries - httpt retries for API server calls
    :param lh_config - lake house configuration
    :param s3_config - s3 configuration
    :param s3_access_secret - s3 access secret
    :param max_files - max files to process
    :param actor_options - actor options
    :param pipeline_id - pipeline id
    :param blocklist_annotation_column_name - name of blocklist annotation column
    :param blocklist_source_url_column_name - name of the source column containing URL
    :param blocklist_blocked_domain_list_path - block domain list path
    :param blocklist_s3_config - block list s3 config (here we are assuming that blocklist info is in S3)
    :param blocklist_s3_access_secret - block list access secret
                    (here we are assuming that blocklist info is in S3, but potentially in the different bucket)
    :return: None
    """
    # create clean_up task
    clean_up_task = cleanup_ray_op(ray_name=ray_name, run_id=dsl.RUN_ID_PLACEHOLDER, server_url=server_url)
    ComponentUtils.add_settings_to_component(clean_up_task, 60)
    # pipeline definition
    with dsl.ExitHandler(clean_up_task):
        # compute execution params
        compute_exec_params = compute_exec_params_op(
            worker_options=ray_worker_options,
            actor_options=actor_options,
        )
        ComponentUtils.add_settings_to_component(compute_exec_params, ONE_HOUR_SEC * 2)
        # start Ray cluster
        ray_cluster = create_ray_op(
            ray_name=ray_name,
            run_id=dsl.RUN_ID_PLACEHOLDER,
            ray_head_options=ray_head_options,
            ray_worker_options=ray_worker_options,
            server_url=server_url,
            additional_params=additional_params,
        )
        ComponentUtils.add_settings_to_component(ray_cluster, ONE_HOUR_SEC * 2)
        ray_cluster.after(compute_exec_params)
        # Execute job
        execute_job = execute_ray_jobs_op(
            ray_name=ray_name,
            run_id=dsl.RUN_ID_PLACEHOLDER,
            additional_params=additional_params,
            # note that the parameters below are specific for NOOP transform
            exec_params={
                "s3_config": s3_config,
                "lh_config": lh_config,
                "max_files": max_files,
                "num_workers": compute_exec_params.output,
                "worker_options": actor_options,
                "pipeline_id": pipeline_id,
                "job_id": dsl.RUN_ID_PLACEHOLDER,
                "blocklist_annotation_column_name": blocklist_annotation_column_name,
                "blocklist_source_url_column_name": blocklist_source_url_column_name,
                "blocklist_blocked_domain_list_path": blocklist_blocked_domain_list_path,
                "blocklist_s3_config": blocklist_s3_config,
            },
            exec_script_name=EXEC_SCRIPT_NAME,
            server_url=server_url,
            prefix=PREFIX,
        )
        ComponentUtils.add_settings_to_component(execute_job, ONE_WEEK_SEC)
        ComponentUtils.set_s3_env_vars_to_component(execute_job, s3_access_secret)
        ComponentUtils.set_s3_env_vars_to_component(execute_job, blocklist_s3_access_secret, prefix=PREFIX)
        execute_job.after(ray_cluster)

    # set image pull secrets
    dsl.get_pipeline_conf().set_image_pull_secrets([k8s_client.V1ObjectReference(name="prod-all-icr-io")])
    # Configure the pipeline level to one week (in seconds)
    dsl.get_pipeline_conf().set_timeout(ONE_WEEK_SEC)


if __name__ == "__main__":
    # Compiling the pipeline
    compiler.Compiler().compile(blocklisting, __file__.replace(".py", ".yaml"))
