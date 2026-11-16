const categories = [
  [
    "code",
    "编程与技术",
    "编程、模型、agent 与开源"
  ],
  [
    "art",
    "艺术与创作",
    "图像、音乐、电影与游戏"
  ],
  [
    "design",
    "设计与产品",
    "视觉、交互与产品体验"
  ],
  [
    "use",
    "应用与效率",
    "工作、学习与实用方法"
  ],
  [
    "business",
    "商业与产业",
    "公司、创业与商业变化"
  ],
  [
    "science",
    "科学与研究",
    "数学、生物与科学进展"
  ],
  [
    "human",
    "人文与社会",
    "人与技术、文化与教育"
  ],
  [
    "policy",
    "政策与治理",
    "规则、版权与社会治理"
  ]
];

const escape = value => String(value).replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));
const name = id => categories.find(category => category[0] === id)?.[1] || id;
const githubLogo = "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M12 .8a11.3 11.3 0 0 0-3.6 22c.6.1.8-.2.8-.5v-2c-3.4.7-4.1-1.5-4.1-1.5-.6-1.4-1.4-1.7-1.4-1.7-1.1-.8.1-.8.1-.8 1.2.1 1.8 1.2 1.8 1.2 1.1 1.8 2.8 1.3 3.6 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.4-5.5-6 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C18.5 4.2 19.5 4.5 19.5 4.5c.6 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.7-5.5 6 .4.4.8 1.1.8 2.2v3.2c0 .3.2.6.8.5A11.3 11.3 0 0 0 12 .8Z\"/></svg>";
const knownSites = {
  "blogs.microsoft.com": [
    "Microsoft",
    "M"
  ],
  "lovable.dev": [
    "Lovable",
    "L"
  ],
  "tessl.io": [
    "Tessl",
    "T"
  ],
  "aisi.gov.uk": [
    "AISI",
    "A"
  ],
  "the-decoder.com": [
    "The Decoder",
    "D"
  ],
  "ithome.com": [
    "IT之家",
    "IT"
  ],
  "oreilly.com": [
    "O’Reilly",
    "O"
  ],
  "pub.sakana.ai": [
    "Sakana AI",
    "S"
  ],
  "sakana.ai": [
    "Sakana AI",
    "S"
  ],
  "arxiv.org": [
    "arXiv",
    "a"
  ],
  "arstechnica.com": [
    "Ars Technica",
    "a"
  ],
  "techcrunch.com": [
    "TechCrunch",
    "TC"
  ],
  "vcresearch.berkeley.edu": [
    "UC Berkeley",
    "B"
  ],
  "marktechpost.com": [
    "MarkTechPost",
    "M"
  ]
};

function platform(url) {
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:') throw Error('来源链接必须使用 HTTPS');
  const host = parsed.hostname.replace(/^www\./, '');
  if (host === 'x.com' || host === 'twitter.com') {
    return {name: 'X', className: 'x', logo: 'X'};
  }
  if (host === 'reddit.com' || host.endsWith('.reddit.com') || host === 'redd.it') {
    return {name: 'Reddit', className: 'reddit', logo: 'R'};
  }
  if (host === 'github.com') return {name: 'GitHub', className: 'github', logo: githubLogo};
  if (host === 'youtube.com' || host === 'youtu.be') {
    return {name: 'YouTube', className: 'youtube', logo: '▶'};
  }
  if (host === 'mp.weixin.qq.com') return {name: '微信', className: 'wechat', logo: '微'};
  const [label, logo] = knownSites[host] || [host, host.slice(0, 1).toUpperCase()];
  return {name: label, className: 'website', logo: escape(logo)};
}
