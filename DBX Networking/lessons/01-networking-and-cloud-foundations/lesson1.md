# Topic 1 - Networking & Cloud Foundations, Explained Simply

> Companion to `lesson.md`, but written for intuition first.
>
> Goal: know how the pieces work in Azure Databricks conversations without
> memorizing port lists, IP ranges, or every Azure setting.

---

## The One Mental Model

Think of an Azure Databricks deployment like a private business campus.

- **CIDR** is the address plan for the campus.
- **VNet** is the campus boundary.
- **Subnets** are the roads or zones inside the campus.
- **NICs** are the network cards that let a VM park on a road and receive an IP.
- **NSG** is the security guard deciding whether traffic is allowed.
- **UDR** is the road sign deciding where traffic goes next.
- **NAT Gateway** is the official exit gate that makes outbound traffic use a stable public IP.
- **DNS** is the phonebook that turns a name into an IP address.
- **Private DNS** is the internal phonebook that sends users to private IPs.
- **Hub-and-spoke** is the city layout: shared services in the hub, Databricks workspaces in spokes.

For Databricks, this matters because classic compute runs in the customer VNet.
So the customer's network rules shape how clusters start, phone home, and reach storage.

---

## 1. CIDR: How Much Address Space Do We Have?

### Simple Explanation

CIDR is just a compact way to describe a block of IP addresses.

Example: `10.179.0.0/16` means "a large private address block starting at
10.179.0.0." You do not need to memorize the math first. Just remember:

- smaller slash number = bigger block
- bigger slash number = smaller block
- `/16` is large
- `/24` is much smaller
- `/26` is small

### Databricks Meaning

In Azure Databricks classic compute, every cluster node needs IP addresses from
the workspace subnets. If the subnets are too small, clusters cannot grow or may
fail to start.

### Plain Customer Explanation

"Before we deploy Databricks into your VNet, we need enough private address space
for the biggest cluster size you expect. If we choose too small a subnet, the
workspace can run out of IPs later."

### What Breaks

If the subnet runs out of IPs:

- clusters stay pending
- autoscaling fails
- users see cluster start failures

This is not usually a Databricks compute bug. It is usually a network capacity
problem.

---

## 2. VNet and Subnets: Where Databricks Lives

### Simple Explanation

A **VNet** is your private network boundary in Azure.

A **subnet** is a smaller section inside that VNet.

Think:

- VNet = gated community
- subnet = street inside the community
- VM = house on the street
- IP address = house number

### Databricks Meaning

For classic Azure Databricks with VNet injection, Databricks uses two delegated
subnets:

- **host subnet** - supports the VM host side
- **container subnet** - supports the Databricks Runtime side

Each node consumes capacity from both sides.

### What Is a NIC?

A **NIC** is a Network Interface Card. In Azure, it is usually a virtual network
adapter attached to a VM.

Simple version:

> A NIC is how a VM plugs into the subnet and gets an IP address.

In Databricks classic compute:

- one node uses a host NIC/IP
- one node uses a container NIC/IP
- this is why subnet sizing affects max node count

### Plain Customer Explanation

"When we place Databricks compute in your VNet, it uses your subnets like parking
spaces. Each cluster node needs space in both the host and container subnets. If
those streets are too short, we run out of parking."

---

## 3. NSG, UDR, and NAT: Allowed, Routed, and Exiting

This is the most important foundation for Databricks networking conversations.

### NSG = Is This Traffic Allowed?

An **NSG** is a rulebook attached to a subnet or NIC.

It answers:

> "Is this traffic allowed or blocked?"

For Databricks, an NSG helps control what the compute subnet can talk to.

Example:

- allow required outbound traffic
- restrict unnecessary inbound traffic
- protect subnet boundaries

Plain customer explanation:

"The NSG is the guard at the subnet. It checks whether traffic is allowed."

### UDR = Where Should This Traffic Go?

A **UDR** is a custom route.

It answers:

> "Which next hop should this traffic take?"

For Databricks, a customer may use UDRs to send outbound traffic through an Azure
Firewall or network appliance.

Example:

- send internet-bound traffic to Azure Firewall
- send storage traffic through a private path
- send on-prem traffic through the hub

Plain customer explanation:

"The UDR is the road sign. It does not decide whether traffic is allowed. It
decides where traffic is sent."

### NAT Gateway = Which Stable IP Do We Exit From?

A **NAT Gateway** gives outbound traffic a stable public source IP.

It answers:

> "When this private compute talks outbound, what public IP should the outside
world see?"

For Databricks, NAT is useful when external systems allowlist a fixed IP.

Plain customer explanation:

"The NAT Gateway is the official exit gate. Cluster traffic leaves through a
known IP, so downstream systems can allowlist it."

### The Memory Hook

| Control | Simple Question | Everyday Analogy |
| --- | --- | --- |
| NSG | Is it allowed? | Security guard |
| UDR | Where should it go? | Road sign / GPS |
| NAT | Which IP does it exit as? | Official exit gate |

### What Breaks

If NSG is wrong:

- traffic is blocked
- clusters may not reach required services

If UDR is wrong:

- traffic goes to the wrong place
- firewall may drop traffic
- cluster startup or storage access can fail

If NAT is missing when needed:

- outbound IP may be unpredictable
- external systems cannot allowlist Databricks reliably

---

## 4. DNS: Names Become IPs

### Simple Explanation

DNS is the phonebook of the network.

Humans use names. Networks use IP addresses.

DNS answers:

> "What IP address does this name point to?"

### Databricks Meaning

When users open a Databricks workspace URL, or when compute reaches storage, DNS
must resolve the name to the correct endpoint.

With Private Link, DNS becomes especially important because the same service name
may need to resolve to a private IP instead of a public IP.

### Plain Customer Explanation

"Private networking is not only about creating a private endpoint. The name also
has to resolve to the private IP. If DNS still points to the public address, the
traffic will not use the private path."

### What Breaks

If DNS is wrong:

- workspace URL may not load privately
- ADLS may resolve to a public endpoint
- Private Link looks configured but traffic still goes the wrong way

First check:

> From inside the VNet, resolve the name and confirm it returns a private IP.

---

## 5. Hub-and-Spoke: How Enterprises Organize Networks

### Simple Explanation

Hub-and-spoke is a common enterprise network layout.

- **Hub** = shared services network
- **Spoke** = workload network

The hub usually contains:

- firewall
- VPN / ExpressRoute gateway
- DNS resolver
- shared inspection tools

The Databricks workspace VNet is usually a spoke.

### Databricks Meaning

Customers often do not want every workspace to build its own firewall, gateway,
and DNS stack. Instead, the Databricks VNet peers to a central hub.

Traffic can then be routed through shared network controls.

### Plain Customer Explanation

"We put Databricks in a spoke VNet and connect it to the hub. The hub gives us
central inspection, routing, DNS, and on-prem connectivity."

### What Breaks

The key gotcha:

> VNet peering is not automatically transitive.

That means spoke A does not automatically talk to spoke B just because both are
connected to the hub. You may need explicit routes through a firewall or network
virtual appliance.

---

## How These Pieces Fit Together for Azure Databricks

### The Story

1. The customer picks a VNet and subnet design.
2. Databricks classic compute is deployed into two delegated subnets.
3. NSGs decide what traffic is allowed.
4. UDRs decide whether traffic goes directly, to firewall, to hub, or elsewhere.
5. NAT gives outbound traffic a stable public IP when needed.
6. DNS decides whether names resolve to public or private endpoints.
7. Hub-and-spoke places Databricks into the customer's enterprise network pattern.

### The Architect One-Liner

"Databricks classic compute runs inside the customer's Azure network, so the same
network foundations apply: CIDR gives us enough addresses, subnets give compute a
place to live, NSGs allow or block traffic, UDRs steer traffic, NAT gives stable
egress, DNS makes private endpoints usable, and hub-and-spoke ties it into the
enterprise network."

---

## Field Troubleshooting: Symptom to First Thought

| Symptom | First Thing To Think |
| --- | --- |
| Cluster will not start | Are the subnets out of IPs? |
| Cluster starts but cannot reach control plane | Did NSG or UDR block outbound path? |
| Customer firewall sees unexpected IP | Is NAT configured for stable egress? |
| Private Link exists but traffic seems public | Does DNS resolve to private IP? |
| Spoke cannot reach on-prem or another spoke | Is hub routing/peering transitive enough? |
| Storage access fails | Is storage reached by the expected route and DNS answer? |

---

## What To Remember

Do not start by memorizing ports.

Start by asking:

1. Where does the compute live?
2. Which subnet does it use?
3. Is traffic allowed?
4. Where is traffic routed?
5. What IP does traffic exit from?
6. What IP does the name resolve to?
7. Is this workspace connected into the enterprise hub correctly?

If you can answer those seven questions, the detailed settings become much easier
to understand.
