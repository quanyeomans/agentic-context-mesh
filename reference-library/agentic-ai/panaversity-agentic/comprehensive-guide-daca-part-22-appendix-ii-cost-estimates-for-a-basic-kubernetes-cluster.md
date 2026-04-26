## Appendix II: Cost Estimates for a Basic Kubernetes Cluster:

https://grok.com/share/bGVnYWN5_1bb223b7-26d5-4e9e-bdbc-4aab3b78d1c3

Civo Kubernetes is the best option, they also give $250 credit for signup (Free 1-2 months of service):

2 “Small” nodes ($20/month) + $10 Load Balancer = $30/month.

https://www.civo.com/pricing


To determine which Kubernetes service provides the **longest duration of validity for free credits**, We’ll evaluate the major managed Kubernetes providers based on their free credit offerings for new users, focusing on the duration of credit validity. The providers considered are **Civo**, **DigitalOcean Kubernetes (DOKS)**, **Google Kubernetes Engine (GKE)**, **Azure Kubernetes Service (AKS)**, **Amazon Elastic Kubernetes Service (EKS)**, and others. The analysis prioritizes the expiration period of promotional credits, as requested, and uses data from your previous questions and relevant web results.

Note: **The Student must have a credit card.**

### Free Credit Validity by Kubernetes Provider
1. **Google Kubernetes Engine (GKE)**:
   - **Credit Amount**: $300 for new users.
   - **Validity**: 90 days (3 months) from account creation.
   - **Details**: The $300 credit can be used across GCP services, including GKE. GKE also offers a free tier with $74.40/month credit for one zonal or Autopilot cluster, which doesn’t expire but is limited to control plane costs. The $300 credit is more flexible for covering nodes, storage, and egress.[](https://github.com/learnk8s/free-kubernetes)[](https://cloud.google.com/kubernetes-engine/pricing)
   - **Cost Context**: A minimum cluster (1 node, 2 vCPUs, 4 GB RAM, 10 GB storage) costs ~$25.69/month with the free tier or ~$98.69/month without (from previous response). The $300 credit covers ~3 months of a minimum cluster without the free tier.

2. **Microsoft Azure Kubernetes Service (AKS)**:
   - **Credit Amount**: $200 for new users.
   - **Validity**: 30 days from account creation.
   - **Details**: The $200 credit applies to all Azure services, including AKS, which has a free control plane. AKS also offers “always free” resources for AI/ML workloads, but this is workload-specific and not broadly applicable to Kubernetes clusters.[](https://github.com/learnk8s/free-kubernetes)[](https://medium.com/techprimers/free-tiers-in-different-cloud-platforms-for-trying-out-kubernetes-2ccda3f296dc)
   - **Cost Context**: A minimum cluster costs ~$32.04/month (previous response). The $200 credit covers ~6 months if used sparingly within 30 days, but the short validity limits its utility.

3. **Amazon Elastic Kubernetes Service (EKS)**:
   - **Credit Amount**: $200 for new users (varies by promotion; some sources mention up to $1,000 for specific AWS programs, but $200 is standard).
   - **Validity**: 30 days from account creation.
   - **Details**: The $200 credit applies to AWS services, including EKS. EKS has no free tier for the control plane ($73/month), making it costlier.[](https://github.com/learnk8s/free-kubernetes)[](https://www.webasha.com/blog/free-cloud-credits-to-learn-cloud-Kubernetes-and-host-your-website)
   - **Cost Context**: A minimum cluster costs ~$105.07/month (previous response). The $200 credit covers ~1.9 months but must be used within 30 days.

4. **DigitalOcean Kubernetes (DOKS)**:
   - **Credit Amount**: $200 for new users (previously $100, updated per recent promotions).
   - **Validity**: 60 days from account creation.
   - **Details**: The $200 credit applies to all DigitalOcean services, including DOKS, which has a free control plane. A referral may be required for the full amount, but the standard offer is now $200.[](https://www.digitalocean.com/pricing/kubernetes)[](https://blog.guybarrette.com/learn-kubernetes-free-cloud-credits)
   - **Cost Context**: A minimum cluster costs ~$25.00/month (previous response). The $200 credit covers ~8 months if used within 60 days, but you’d consume ~$50 for a minimum cluster over 2 months.

5. **Civo Kubernetes**:
   - **Credit Amount**: $250 for new users.
   - **Validity**: Until the end of the following month after claiming (e.g., claim on April 22, 2025, expires May 31, 2025, roughly 30-60 days depending on claim date).
   - **Details**: The $250 credit applies to Civo’s Kubernetes and other services, with a free control plane. The validity is shorter if claimed late in a month (e.g., April 29 to May 31 is ~33 days).[](https://github.com/learnk8s/free-kubernetes)[](https://x.com/CivoCloud/status/1410179516752752647)
   - **Cost Context**: A minimum cluster costs ~$21.00/month (previous response). The $250 credit covers ~11 months but must be used within ~30-60 days.

6. **Linode Kubernetes Engine (LKE)**:
   - **Credit Amount**: $100 for new users.
   - **Validity**: 60 days from account creation.
   - **Details**: The $100 credit applies to Linode services, including LKE.[](https://github.com/learnk8s/free-kubernetes)
   - **Cost Context**: A minimum cluster (1 node, 2 vCPUs, 4 GB RAM) costs ~$24-$30/month (based on Linode’s node pricing, similar to DOKS). The $100 credit covers ~3-4 months within 60 days.

7. **IBM Cloud Kubernetes Service**:
   - **Credit Amount**: $200 (standard), up to $1,000 for specific services (e.g., VPC-based services for 180 days).
   - **Validity**: 30 days for the $200 credit; 180 days (6 months) for the $1,000 VPC credit.
   - **Details**: The $200 credit applies to all IBM Cloud services, including Kubernetes, but expires quickly. The $1,000 credit for Virtual Server for VPC, Block Storage, and Image Service is valid for 180 days, the longest duration found, but it’s unclear if it fully applies to Kubernetes clusters (likely partial coverage for nodes/storage). A single-node cluster is free for 30 days, but this is a trial, not a credit.[](https://www.ibm.com/cloud/free/kubernetes)
   - **Cost Context**: A minimum cluster cost is ~$25-$35/month (estimated, similar to AKS/DOKS). The $200 credit covers ~6-8 months within 30 days; the $1,000 VPC credit could cover ~2-3 years if applicable, but likely less for Kubernetes-specific costs.

8. **Alibaba Cloud Container Service for Kubernetes**:
   - **Credit Amount**: $300 for new users.
   - **Validity**: 12 months (1 year).
   - **Details**: The $300 credit applies to Alibaba Cloud services, including Kubernetes, and is noted for its long validity. Kubernetes is also in their “always free” resource list for limited workloads.[](https://medium.com/techprimers/free-tiers-in-different-cloud-platforms-for-trying-out-kubernetes-2ccda3f296dc)
   - **Cost Context**: A minimum cluster cost is ~$25-$40/month (estimated, based on similar providers). The $300 credit covers ~7-12 months over a full year.

### Ranking by Credit Validity Duration
| Provider | Credit Amount | Validity Duration | Approx. Coverage for Minimum Cluster ($/month) |
|----------|---------------|-------------------|-----------------------------------------------|
| **Alibaba Cloud** | $300 | **12 months** | ~7-12 months ($25-$40/month) |
| **IBM Cloud** | $1,000 (VPC) | **180 days** | ~2-3 years (if applicable, ~$25-$35/month) |
| **GKE** | $300 | **90 days** | ~3 months ($98.69/month w/o free tier) |
| **DOKS** | $200 | **60 days** | ~8 months ($25/month, ~$50 in 60 days) |
| **Linode (LKE)** | $100 | **60 days** | ~3-4 months ($24-$30/month) |
| **Civo** | $250 | **~30-60 days** | ~11 months ($21/month, within ~30-60 days) |
| **AKS** | $200 | **30 days** | ~6 months ($32.04/month, within 30 days) |
| **EKS** | $200 | **30 days** | ~1.9 months ($105.07/month, within 30 days) |

### Longest Duration of Validity
- **Alibaba Cloud** offers the **longest validity** at **12 months** for its $300 credit, allowing you to spread usage over a full year. This is ideal for a minimum Dapr-enabled cluster (~$25-$40/month), covering ~7-12 months depending on configuration.[](https://medium.com/techprimers/free-tiers-in-different-cloud-platforms-for-trying-out-kubernetes-2ccda3f296dc)
- **IBM Cloud** follows with **180 days** (6 months) for its $1,000 VPC credit, potentially covering ~2-3 years of a minimum cluster if fully applicable to Kubernetes costs (though likely partial). The $200 standard credit is less competitive at 30 days.[](https://www.ibm.com/cloud/free/kubernetes)
- **GKE** ranks third with **90 days** for $300, covering ~3 months of a minimum cluster without the free tier.[](https://github.com/learnk8s/free-kubernetes)

### Recommendation
For the **longest duration of free credit validity**, choose **Alibaba Cloud** with its **12-month $300 credit**, which supports a minimum Kubernetes cluster (~$25-$40/month) for ~7-12 months. Sign up at alibabacloud.com/campaign to claim the credit, and verify eligibility for Kubernetes usage. If Alibaba’s ecosystem is unfamiliar, **IBM Cloud’s 180-day $1,000 VPC credit** is a strong alternative, though confirm its applicability to Kubernetes nodes/storage. For providers you’ve previously asked about:
- **DOKS**: 60 days ($200) is decent but shorter than Alibaba/IBM.[](https://www.digitalocean.com/pricing/kubernetes)
- **Civo**: ~30-60 days ($250) is the shortest among competitive options.[](https://x.com/CivoCloud/status/1410179516752752647)

### Notes
- **Credit Usage**: Delete clusters when not in use to maximize credit longevity, as 24/7 clusters (e.g., $21-$105/month) consume credits quickly. Use automation (e.g., Terraform) to spin up/down clusters.
- **Eligibility**: All providers require a credit card for signup, but you’re not charged until credits are exhausted.[](https://github.com/learnk8s/free-kubernetes)
- **Verification**: Check credit terms on provider websites (e.g., alibabacloud.com, ibm.com/cloud) or billing dashboards post-signup, as promotions may vary by region or date.
- **Critical Note**: Some sources (e.g.,) are outdated (2020) or inconsistent (e.g., Azure’s 12-month claim vs. 30-day standard). I prioritized recent data and cross-checked with provider sites where possible. Alibaba’s 12-month validity is well-documented, but always confirm terms at signup.[](https://medium.com/techprimers/free-tiers-in-different-cloud-platforms-for-trying-out-kubernetes-2ccda3f296dc)


---