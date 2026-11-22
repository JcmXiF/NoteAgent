---
type: concept
title: KL散度（相对熵）
created: 2026-09-11
updated: 2026-09-16
tags: [KL散度, 相对熵, 信息散度, 信息论, 概率分布, 熵, 分布差异, 不确定性]
related: [信息熵, 互信息, 条件熵, 联合熵, 交叉熵, 自信息, elements-of-information-theory, 信源编码定理]
sources: [raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b3, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b4, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b18, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b19, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b20, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b21, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b22, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b23, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b24, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b26, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b27, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b55, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b56, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b57, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b58, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b59, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b60, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b61, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b62, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b63, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b1, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b2, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b3, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b4, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b6, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b1, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b3, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b4, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b6, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b8, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b10, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b11, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b9, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b13, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b15, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b16, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b17, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b19, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b7, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b8, raw/sources/web/cnblogs.com/10741462.html-7d86a2166fec.html#b9, raw/sources/web/hsinjhao.github.io/KL-DivergenceIntroduction-e6281c941d9b.html#b5, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b1, raw/sources/web/zh.wikipedia.org/相对熵-d112ccdf1344.html#b2, raw/sources/web/12906163.html-9dd0e3d5e910.html#b1, raw/sources/web/12906163.html-9dd0e3d5e910.html#b2, raw/sources/web/12906163.html-9dd0e3d5e910.html#b3, raw/sources/web/12906163.html-9dd0e3d5e910.html#b4, raw/sources/web/12906163.html-9dd0e3d5e910.html#b9, raw/sources/web/12906163.html-9dd0e3d5e910.html#b10, raw/sources/web/12906163.html-9dd0e3d5e910.html#b11, raw/sources/web/12906163.html-9dd0e3d5e910.html#b12, raw/sources/web/charlesliuyx.github.io/什么是信息熵、交叉熵和相对熵-5a5592d5bf77.html#b1, raw/sources/web/charlesliuyx.github.io/什么是信息熵、交叉熵和相对熵-5a5592d5bf77.html#b2, raw/sources/web/charlesliuyx.github.io/什么是信息熵、交叉熵和相对熵-5a5592d5bf77.html#b4, raw/sources/web/charlesliuyx.github.io/什么是信息熵、交叉熵和相对熵-5a5592d5bf77.html#b5, raw/sources/web/charlesliuyx.github.io/什么是信息熵、交叉熵和相对熵-5a5592d5bf77.html#b8, raw/sources/web/charlesliuyx.github.io/什么是信息熵、交叉熵和相对熵-5a5592d5bf77.html#b9, raw/sources/web/charlesliuyx.github.io/什么是信息熵、交叉熵和相对熵-5a5592d5bf77.html#b10, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b3, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b4, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b5, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b6, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b7, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b8, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b9, raw/sources/web/jiqizhixin.com/7afc5fc3-a61c-4238-b496-9c39392a91fe-f4bf20e0366f.html#b10, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b1, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b2, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b3, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b7, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b8, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b9, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b10, raw/sources/web/zh.wikipedia.org/熵_(信息论)-dd3c946915be.html#b11, "raw/sources/web/Elements of Information Theory.pdf#p1", "raw/sources/web/Elements of Information Theory.pdf#p2", "raw/sources/web/Elements of Information Theory.pdf#p3", "raw/sources/web/Elements of Information Theory.pdf#p4", "raw/sources/web/Elements of Information Theory.pdf#p5", "raw/sources/web/Elements of Information Theory.pdf#p6", "raw/sources/web/Elements of Information Theory.pdf#p7", "raw/sources/web/Elements of Information Theory.pdf#p8"]
---
# KL散度（相对熵）

KL散度（Kullback-Leibler divergence，简称 KLD）是同一个量在多个学科语境下反复出现的结果：在消息系统中它称为相对熵，在统计模型推断中它称为消息增益。它刻画的不是单个随机变量的不确定性，而是两个概率分布之间的差异——就定义本身而言，KL散度是**两个概率分布 P 和 Q 差别的非对称性度量**，度量的是「用分布 Q 去近似分布 P」所要额外付出的编码代价。

它被提出的典型场景是这样的：当我们**观察数据**时，真正支配数据的分布往往**太复杂**，难以直接使用；于是我们希望用一个**更简单的、近似的分布**去代替它。K-L 散度正是在这种「以近似分布替代复杂分布」的处境下被引入的——它关心的是这种替代究竟意味着什么、代价有多大。

本页沿「名称与别名 → 直观场景与编码解释 → 预备知识（信息量与熵）→ 定义（离散、连续与概率测度）→ 性质（正定性、不对称性）→ 应用与相关脉络」的顺序展开，其中离散定义部分以 [[concepts/信息熵]] 为前提。

## 名称与别名：KL散度 / 相对熵 / 消息增益 / 信息散度

*（原条目在此处配有一幅示意图，无图注，作为本页开篇配图。）*

K-L 散度与相对熵是同一个量的两个名字。英文全称 Kullback-Leibler Divergence、中文简称 K-L 散度（KLD）、以及相对熵，指向的是同一个概念，而不是三个彼此独立的对象；读到「相对熵」时应当理解为「K-L 散度」的另一种称呼。

正因为它出现在多个学科语境下，也累积了一串别名：

- 在**消息系统**中，它称为**相对熵**（relative entropy）；
- 在**连续时间序列**中，它称为**随机性**（randomness）；
- 在**统计模型推断**中，它称为**消息增益**（information gain）；
- 此外，它也称**信息散度**（information divergence）。

> **来源范围说明（两版各自剪辑的范围）**
> - **既有版本**：生成该条目的剪辑只给出了名称本身，以及若干与名称一同出现的上下文短语——「观察数据」「太复杂的分布」「更简单的、近似的分布」。这些短语连同名称一起出现，提示该概念的引入与观察数据、以及复杂分布同更简单的近似分布之间的关系有关，但剪辑中并未展开这一语境，也没有给出任何定义式或度量公式；公式与推导由本页后续各节（离散、连续与测度形式）补足。
> - **新生成版本**：其片段同样只给出概念名称（K-L 散度 / 相对熵 / Kullback-Leibler Divergence）与上述提出场景的关键短语，因此该版本在该节只交代定义与直觉，不展开公式与数值；其引入同样不附加公式。

## 直观场景与编码解释

### 用更简单的近似分布顶替复杂分布

K-L 散度被放在「观察数据、用近似分布取代复杂分布」的场景下来理解：当真实的分布太复杂时，我们用更简单、近似的分布去代替它，而 K-L 散度正用来描述这种「用近似分布顶替真实分布」的关系。这是理解该概念的一条直观入口，具体的度量写法要回到下面的求和式与积分式。

### 编码解释：用 Q 编码服从 P 的样本所需的额外平均比特数

就定义本身而言，KL散度是**两个概率分布 P 和 Q 差别的非对称性度量**。更具体地说，它是用来度量：**使用基于 Q 的分布来编码服从 P 的分布的样本时，所需的额外的平均比特数**。

这里的两个分布各有典型角色：**P 表示数据的真实分布**，而 **Q 表示数据的理论分布、估计的模型分布，或 P 的近似分布**。换言之，KL散度刻画的是「用 Q 代替 P」在编码代价上要多付出多少——这也是它被称为「消息增益」的由来：使用 Q 而非真实分布 P 所多出的那部分信息量。

## 预备知识：信息量与熵

> 新生成版本在此声明其视角范围：该笔记仅从机器学习的角度来理解熵、条件熵、相对熵、交叉熵和互信息这一组概念。在给出它们的定义之前，先固定下「信息量」应当具备的性质与熵的记号——后续的各个量都建立在这个信息量函数之上。

### 信息量应当具备的三条性质

**第一，信息量与事件发生的概率有关。** 直觉上，事件的信息量应当与它的概率挂钩：概率增大，信息量减小；概率减小，信息量增大。因此可以认为信息量是概率的某个（递减）函数。当然，此时也可能设想成概率的另一种关系，这一点留到后面再说。

**第二，独立事件同时发生的信息量应当是相加的。** 考虑同时观察两个事件所获得的信息量：若二者不相关（独立），它们各自的信息量与合起来的信息量之间应满足什么关系？例如「你听说广州下雪了」与「你接到妈妈的电话叫你回家吃饭」，这两件事是独立的，所以它们的信息量应当是各自信息量之和的关系。

**第三，信息量函数应当大于等于零。** 如果一个信息量函数可以取负值，意味着收到一条消息反而会失去信息；但以「接到妈妈的电话」为例，你至多觉得这个电话对你来说没有任何信息，而你的信息是不会跑到你妈妈那里去的。所以这个函数应当是大于等于 0 的。

回过头看，前面的第一种设想就不太合理了，理由正在于信息量是一个大于等于 0 的数。

综上，就想到一个（对数形式的）函数——新生成版本所据片段中没有给出它的具体表达式，此处不补写。这个函数是后面「1. 信息熵」等一系列量的出发点。

### 熵：词源、定义与别名

**词源**：entropy（熵）最初来源于热力学；熵的概念最早起源于**物理学**，用于度量一个热力学系统的无序程度。在**信息论**里，熵则是对不确定性的测量。

**信息论中的定义**：在信息论中，熵是**接受的每条消息中所包含的信息的平均值**。它又被称为**信息熵**、**信源熵**、**平均自信息量**。

熵可以被理解为**不确定性的度量**：熵越大，信源的分布越随机。

这里的熵是下文理解 KL 散度的必要背景——相对熵衡量的正是两个分布之间「多出来的不确定性」，因此先明确单个分布的熵如何度量不确定性，才谈得上比较两个分布。关于熵的来龙去脉，见 [[concepts/信息熵]]。

### 记号：P、E 与 I(X)

其中，P 为 X 的概率质量函数（probability mass function），E 为期望函数，而 I(X) 是 X 的信息量（又称为**自信息**）。需要强调的是，I(X) 本身是个随机变数——它随 X 的取值而变，因此可以再对它取期望。

### 离散随机变量的熵与 0 log 0 约定

若一个离散随机变量 $\mathbf{X}$ 的可能取值为

$$X=\{x_1,x_2,\cdots,x_n\},$$

而对应的概率为

$$p_i = p(X = x_i),$$

则随机变量 $\mathbf{X}$ 的熵定义为

$$H(X) = -\sum_{i=1}^{n} p(x_i)\log p(x_i).$$

求和是对取值集合 $X=\{x_1,\dots,x_n\}$ 中的每一个 $x_i$ 进行的，每一项由该取值的概率 $p(x_i)$ 加权。新生成版本对此的读法是：熵是自信息在概率分布 P 下的期望，这正是用 E 记号书写熵定义的原因（该版本所据片段中，求和形式的表达式落在源图上、文字层缺失，未作改写；上式以既有版本给出的可提取文字形式为准）。

为避免 $\log 0$ 带来的无定义，这里规定：当 $p(x_i) = 0$ 时，

$$p(x_i)\log p(x_i) = 0 .$$

也就是说，概率为零的取值不参与求和，而不是使求和式失效。新生成版本补充说明：$p_i = 0$ 时，对于一些 $i$ 值，对应的被加数 $0\log_b 0$ 的值将会是 0，**这与极限一致**。这一约定是后续所有基于熵的量的前提，KL 散度同样依赖它来避免出现 $0 \cdot \log 0$ 的未定义情形。

### 对数的底与熵的单位

在这里 b 是对数所使用的底，通常是 2、自然常数 e，或是 10。底的选择只改变熵的数值单位，不改变其含义：

| 底 | 熵的单位 |
| --- | --- |
| $b = 2$ | bit |
| $b = e$ | nat |
| $b = 10$ | Hart |

### 条件熵

还可以定义事件 X 与 Y 分别取 $x_i$ 和 $y_j$ 时的条件熵；其具体表达式同样落在源图上（新生成版本所据片段的文字层缺失，故不在此补写）。其含义是：在已知 Y 的取值之后，X 仍剩下的不确定性。

这些量（概率质量函数、期望、自信息、熵的单位与条件熵）是后续写出 KL 散度表达式时的直接构件。

### 熵的直观、英语文本的例子与压缩意义

在信息世界，熵越高，能传输的信息越多；熵越低，意味着能传输的信息越少。

一个常被引用的例子是英语文本。英语文本数据流的熵比较低，因为英语很容易读懂，也就是说很容易被预测：即便我们不知道下一段英语文字是什么内容，也能很容易地预测——字母 e 总是比字母 z 多，字母组合 qu 的可能性总是超过 q 与任何其它字母的组合。如果未经压缩，一段英文文本的每个字母需要 8 个比特来编码（英文输入法用 8 个比特表达一共 256 个字母及符号），但实际上英文文本的熵大概只有 4.7 比特。

这引出了熵与压缩的关系。如果压缩是**无损**的，即通过解压缩可以百分之百地恢复初始消息内容，那么压缩后的消息携带的信息和未压缩的原始消息一样多。而压缩后的消息可以通过较少的比特传递，因此压缩消息的每个比特能携带更多的信息，也就是说压缩信息的熵更加高。熵更高意味着比较难于预测压缩消息携带的信息，原因在于压缩消息里面没有冗余，即每个比特的消息携带了一个比特的信息。香农的**信源编码定理**揭示了，任何无损压缩技术不可能让一比特的消息携带超过一比特的信息。消息的熵乘以消息的长度，决定了消息可以携带多少信息。

这一小节只提供背景直观：熵刻画的是**不确定性**（也就是可预测程度）。KL散度正是在这个基础上比较两个分布之间的差异。

## KL 散度的定义

### 离散情形：$p$ 相对 $q$ 的相对熵

若有两个随机变量 $\mathbf{P}$、$\mathbf{Q}$，且其概率分布分别为 $p(x)$、$q(x)$，则 $p$ 相对 $q$ 的相对熵为

$$D(p\|q) = \sum_{i=1}^{n} p(x_i)\log\frac{p(x_i)}{q(x_i)} .$$

把它和上一节的熵对照可以看出：结构上仍是以 $p$ 的取值为索引、以 $p(x_i)$ 为权重的求和，区别只在于每一项里除的是 $q(x_i)$，于是两个分布在这一项上「不一致」的程度被对数直接记录了下来。定义中「$p$ 相对 $q$」的顺序是写进式子里的：$p$ 既提供权重、又是被比较的一方，$q$ 只出现在分母，因此在相对熵的定义中两个随机变量的位置不能随意互换。上一节对 $p(x_i)=0$ 的规定在这里同样保证了求和式的每一项都有意义。

需要强调的是，它衡量的是分布 $p$ 相对于分布 $q$ 的一个量，而不是两个分布对称距离意义上的度量。

**名称与去向**：这一量也称 Kullback–Leibler 散度；在需要衡量分布差异的场合会用到它。KL 散度无法脱离熵的记号单独讨论，上述记号（概率质量函数、期望、自信息、对数底与条件熵）即为写出该表达式的直接构件。

### 连续情形：密度积分

对于连续随机变量，其概率分布 $P$ 和 $Q$ 的 KL 散度可按积分方式定义为

$$
D_{\mathrm{KL}}(P\|Q)=\int_{-\infty}^{\infty}p(x)\ln\frac{p(x)}{q(x)}\,{\rm d}x,
$$

其中 $p$ 和 $q$ 分别表示分布 $P$ 和 $Q$ 的密度。也就是说，连续情形下的 KL 散度是把上一节的求和换成对密度的积分，被积函数仍是 $p$ 乘以 $p$ 与 $q$ 之比的对数。

### 一般概率测度形式：Radon–Nikodym 导数

更一般地，若 $P$ 和 $Q$ 为集合 $X$ 上的概率测度，且 $P$ 关于 $Q$ 绝对连续，则从 $P$ 到 $Q$ 的 KL 散度定义为

$$
D_{\mathrm{KL}}(P\|Q)=\int_{X}\ln\frac{{\rm d}P}{{\rm d}Q}\,{\rm d}P,
$$

这里的被积对象是 Radon–Nikodym 导数 $\frac{{\rm d}P}{{\rm d}Q}$。这一形式把离散情形的求和与连续情形的密度积分统一起来：离散分布对应计数测度下的密度（概率质量函数），连续分布对应勒贝格测度下的密度，而一般测度情形则直接以 $\frac{{\rm d}P}{{\rm d}Q}$ 代替密度之比 $p/q$；在 $P$、$Q$ 有密度 $p$、$q$ 的情形下，一般测度下的定义即退化为前一种积分写法。绝对连续这一条件正是保证该导数存在、从而上述积分有意义的前提。

可以顺带提到，若把上式中的 $\ln\frac{{\rm d}P}{{\rm d}Q}$ 展开，就回到散度「期望对数比」的直观读法——只是这里的期望是关于 $P$ 取的。相关的绝对连续、Radon–Nikodym 导数概念是理解该定义的工具。

## 性质

### 性质一：正定性由 Gibbs 不等式直接得出

KL 散度（相对熵）的正定性可以**由 Gibbs 不等式直接得出**。也就是说，只要先接受 Gibbs 不等式，KL 散度非负这一性质就不需要另行独立证明。

**Gibbs 不等式的条件：**

若
$$\sum_{i=1}^{n} p_i = \sum_{i=1}^{n} q_i = 1,$$
且
$$p_i,\ q_i \in (0,1],$$
则有 Gibbs 不等式成立。

这里有两组前提，缺一不可：一是两组量各自求和都为 1（即 $p$ 与 $q$ 都是概率分布），二是每个分量都落在 $(0,1]$ 内（严格为正且不超过 1）。注意第二个条件排除了 $p_i$ 或 $q_i$ 取 0 的情形。

**等号成立条件：**

Gibbs 不等式取等号，**当且仅当对所有 $i$ 有 $p_i = q_i$**，即 $p_i = q_i\ \forall i$。换句话说，当两组概率分布完全相同（逐分量相等）时等号成立，其余情况下不等式严格成立。

**逻辑链：**

由 Gibbs 不等式在「$\sum p_i = \sum q_i = 1$ 且 $p_i, q_i \in (0,1]$」下成立，且等号仅在 $p_i = q_i\ \forall i$ 时取到，KL 散度的正定性随之直接得到：非负性来自不等式本身，而「等于零当且仅当两个分布相同」则来自等号成立条件。

Gibbs 不等式的证明可见后文附录。

> 新生成版本同样把非负性、不对称性归入本页后续小节，并注明其片段不展开这两条性质的证明。

### 性质二：不对称性——KL 散度不是真正的度量或距离

KL 散度并不是一个真正的度量或者距离，因为它不具有对称性。也就是说，交换两个分布的位置后，所得的值一般并不相等，因此它不满足「距离」这一概念所要求的对称性条件。

在各种散度中，Jensen-Shannon divergence（JS散度）是对称的——这一点与 KL 散度形成对照。

对 KL 散度不对称性的直观解释，可见原条目所附链接。

#### 从公式本身即可读出不对称

很自然地，一些同学会把 K-L 散度看作不同分布之间距离的度量。这是不对的，因为从 K-L 散度的计算公式就可以看出它不符合对称性——而距离度量应该满足对称性。这一条不需要额外的实验就能从定义本身读出来：公式里两个分布的位置并不对等，交换它们并不保持取值不变（与上一节离散定义中「$p$ 提供权重、$q$ 只出现在分母」的观察一致）。

#### 具体例子：二项式近似下两个方向不相等

把这一判断放到具体例子上：如果用上面观察到的数据分布来近似二项式分布，就会得到

```
Dkl(Observed || Binomial) != Dkl(Binomial || Observed)
```

也就是说，两个方向并不相等。把两个分布记作 p 和 q，则用 p、q 这一方向与用 q、p 这一方向得到的数值是不一样的；散度把「用谁去近似谁」这件事区分开了，而距离不会。

由于不满足对称性，K-L 散度只能作为一种有方向的「散度」来使用，不能当作分布之间的距离来理解——这正是「散度并非距离」的含义。

#### 小结

综合起来，KL 散度刻画的是「用 Q 代替 P」的代价：它非负，且仅在两个分布逐分量相同时为零，却因为不满足对称性而不构成严格意义上的距离——使用时必须指明方向。

## 应用

### 变分自编码器（Variational AutoEncoder）

一处应用是变分自编码器（Variational AutoEncoder）。既有版本在此只把 VAE 作为引出用法的背景一笔带过，不展开其训练细节；新生成版本补充了它在变分推断中的代表性用途：编码器输出一个分布，需要把它与先验分布之间的「相对熵」作为目标的一部分来约束。具体性质（非负性、不对称性等）以及它与交叉熵、熵之间的关系，属于本页前述与后续小节的内容。

### 相关概念：互信息

在相关概念方面，互信息与本概念并列提及，但未给出两者之间的等式或推导，因此这里只作提示而不下结论：更细的关系需要回到相应条目 [[concepts/互信息]] 去看。

### 延伸线索

可顺带作为延伸线索列出的还有：自信息（信息量）、条件熵、联合熵、交叉熵、Elements of Information Theory（Cover & Thomas）、以及信源编码定理与无损压缩——它们与相对熵同处一个概念网络，本页不展开。

## 相关脉络与延伸

在标准的信息论教材编排中，KL散度并不是孤立的一节。以 Cover 与 Thomas 的 *Elements of Information Theory*（第 2 版）为例，第 2 章题为 “Entropy, Relative Entropy, and Mutual Information”，把熵（§2.1）、联合熵与条件熵（§2.2）、相对熵与互信息（§2.3）依次放在同一章内讨论，随后在 §2.4 交代熵与互信息之间的关系，并在 §2.5 给出 “Chain Rules for Entropy, Relative Entropy, and Mutual Information”——即熵、相对熵、互信息各自都有相应的链式法则。这条目录线索说明：理解 KL散度时，把它与熵、互信息并置，并留意三者共有的链式展开结构，是教材层面的自然读法。

若要顺着这条脉络继续，可就近延伸到 [[联合熵]]、条件熵与互信息，以及同章后续的 Jensen 不等式、对数求和不等式与数据处理不等式等工具。
