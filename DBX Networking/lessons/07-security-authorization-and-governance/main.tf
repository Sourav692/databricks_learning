# =============================================================================
# Lesson 7.4 — Cluster Policies & Access Modes (hands-on companion)
#
# Goal: as code, stand up a cost-capped, Unity-Catalog-compliant compute policy,
#       grant a group permission to use it, and lock down the object ACLs around
#       a shared cluster, a job, and a cluster policy.
#
# Prerequisites:
#   - Azure Databricks workspace on the PREMIUM plan (policies + ACLs are Premium).
#   - `databricks` provider authenticated at WORKSPACE scope by a workspace admin
#     (e.g. DATABRICKS_HOST + DATABRICKS_TOKEN, or Azure CLI auth).
#   - The referenced groups must already exist (SCIM-provisioned / account groups):
#       data-engineers, analysts, platform-oncall.
#
# What it provisions:
#   - A "Team Standard" all-purpose cluster policy (forces Standard access mode,
#     caps DBUs/hr, pins runtime, requires a cost-center tag).
#   - CAN_USE grant on the policy to the data-engineers group.
#   - Cluster + job ACLs (least privilege).
#
# Cost caveat: this file does not create running compute by default; the example
#   cluster resource is commented out. Uncomment it to test enforcement, and it
#   will incur DBU + Azure VM charges while running.
# =============================================================================

terraform {
  required_providers {
    databricks = { source = "databricks/databricks", version = "~> 1.0" }
  }
}

provider "databricks" {
  # Configure via env vars (DATABRICKS_HOST / DATABRICKS_TOKEN) or Azure CLI auth.
}

# -----------------------------------------------------------------------------
# 1) The cost-capped, UC-compliant cluster policy
# -----------------------------------------------------------------------------
resource "databricks_cluster_policy" "team_standard" {
  name = "Team Standard (UC, cost-capped)"

  definition = jsonencode({
    # Only all-purpose compute may be created from this policy.
    "cluster_type" = { type = "fixed", value = "all-purpose" }

    # FORCE Unity Catalog compliance: USER_ISOLATION == Standard access mode.
    # (Use "SINGLE_USER" instead for a Dedicated/ML policy.)
    "data_security_mode" = { type = "fixed", value = "USER_ISOLATION", hidden = true }

    # THE COST CAP — max DBUs/hour incl. driver. New policy form: maxValue only.
    "dbus_per_hour" = { type = "range", maxValue = 50 }

    # Kill idle clusters; users can't override.
    "autotermination_minutes" = { type = "fixed", value = 30, hidden = true }

    # Bound the fleet size.
    "autoscale.max_workers" = { type = "range", maxValue = 12, defaultValue = 4 }

    # Only approved, cost-sane instance types.
    "node_type_id" = {
      type         = "allowlist"
      values       = ["Standard_DS3_v2", "Standard_DS4_v2", "Standard_D8s_v3"]
      defaultValue = "Standard_DS3_v2"
    }

    # Pin to the latest LTS runtime, dynamically resolved.
    "spark_version" = { type = "fixed", value = "auto:latest-lts", hidden = true }

    # No ad-hoc instance pools.
    "instance_pool_id" = { type = "forbidden", hidden = true }

    # Require a cost-center tag from an approved set, or the cluster won't launch.
    "custom_tags.cost_center" = { type = "allowlist", values = ["9999", "9921", "9531"] }
  })

  # Cap how many clusters one user can create under this policy.
  max_clusters_per_user = 5
}

# -----------------------------------------------------------------------------
# 2) Grant CAN_USE on the policy (without this, non-admins can't see it)
# -----------------------------------------------------------------------------
resource "databricks_permissions" "team_standard_use" {
  cluster_policy_id = databricks_cluster_policy.team_standard.id

  access_control {
    group_name       = "data-engineers"
    permission_level = "CAN_USE"
  }
}

# -----------------------------------------------------------------------------
# 3) (Optional) A shared cluster created UNDER the policy + its ACL
#    Uncomment to deploy. Running compute incurs cost.
# -----------------------------------------------------------------------------
# data "databricks_spark_version" "lts" { long_term_support = true }
#
# resource "databricks_cluster" "shared" {
#   cluster_name            = "team-shared"
#   policy_id               = databricks_cluster_policy.team_standard.id
#   spark_version           = data.databricks_spark_version.lts.id
#   node_type_id            = "Standard_DS3_v2"
#   data_security_mode      = "USER_ISOLATION"   # Standard access mode (UC-compliant)
#   autotermination_minutes = 30
#   autoscale {
#     min_workers = 1
#     max_workers = 4
#   }
#   custom_tags = { cost_center = "9999" }
#   apply_policy_default_values = true            # apply policy defaults via API
# }
#
# resource "databricks_permissions" "cluster_acl" {
#   cluster_id = databricks_cluster.shared.id
#   access_control { group_name = "data-engineers";  permission_level = "CAN_RESTART" }
#   access_control { group_name = "platform-oncall"; permission_level = "CAN_MANAGE" }
# }

# -----------------------------------------------------------------------------
# 4) (Optional) A job ACL — analysts may trigger runs, owner stays as-is.
#    Replace job_id with a real job, or wire to a databricks_job resource.
# -----------------------------------------------------------------------------
# resource "databricks_permissions" "job_acl" {
#   job_id = "<your-job-id>"
#   access_control {
#     group_name       = "analysts"
#     permission_level = "CAN_MANAGE_RUN"   # NOTE: Run Now executes as the job OWNER
#   }
# }

# Secret-scope ACLs are managed via the CLI/REST API, not this provider's
# object-permissions surface in the same way — see the lesson:
#   databricks secrets put-acl --scope prod-kv --principal data-engineers --permission READ
