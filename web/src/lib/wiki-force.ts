/**
 * Laying out a link graph by simulation.
 *
 * Pages push each other apart, links pull their ends together, and everything is
 * drawn gently towards the middle; repeating that settles the graph into
 * something readable. The numbers were tuned against real wikis, so they are not
 * meant to be adjusted by eye.
 *
 * Pure: it reads and writes plain numbers, and never touches the DOM. That is what
 * lets the drawing loop step it sixty times a second without React being involved
 * — the view reads positions out of these objects and writes them straight to the
 * elements it already made.
 */

export type ForceNode = {
  id: string;
  radius: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
};

/** How much room a page gets for itself, which grows with how connected it is. */
export function nodeRadiusForDegree(degree: number): number {
  return 10 + Math.min(16, Math.sqrt(Math.max(0, degree)) * 5);
}

/**
 * Where the pages start, spread evenly around the middle.
 *
 * A ring rather than the same point: pages that begin in the same place have no
 * direction to separate along, and the first steps would push them apart at
 * random.
 */
export function seedForceNodes(
  items: Array<{ id: string; radius: number }>,
  width: number,
  height: number,
): ForceNode[] {
  const cx = width / 2;
  const cy = height / 2;
  const count = items.length;
  const ring = Math.min(width, height) * (count < 2 ? 0 : 0.28);
  return items.map((item, index) => {
    const angle = count === 0 ? 0 : (index / count) * Math.PI * 2;
    return {
      id: item.id,
      radius: item.radius,
      x: cx + ring * Math.cos(angle),
      y: cy + ring * Math.sin(angle),
      vx: 0,
      vy: 0,
    };
  });
}

/**
 * Moves the simulation on by one step.
 *
 * The page being dragged is held still, and everything else is pulled towards the
 * middle so a graph that has drifted does not leave the view.
 */
export function stepForceLayout(
  nodes: ForceNode[],
  links: Array<{ source: string; target: string }>,
  width: number,
  height: number,
  pinnedId: string | null,
  /** Room the drawing needs around a page, e.g. for its name and its counts. */
  margin: { x: number; y: number } = { x: 0, y: 0 },
): void {
  const cx = width / 2;
  const cy = height / 2;
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const repulsion = 4200;
  const spring = 0.025;
  const rest = 110;
  const center = 0.012;
  const damp = 0.82;

  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const a = nodes[i];
      const b = nodes[j];
      let dx = a.x - b.x;
      let dy = a.y - b.y;
      let dist = Math.hypot(dx, dy);
      // Two pages exactly on top of each other have no direction to separate
      // along, so they are given one rather than left stuck.
      if (dist < 0.01) {
        dx = 0.4;
        dy = 0.4;
        dist = Math.hypot(dx, dy);
      }
      const minDist = a.radius + b.radius + 8;
      const force =
        repulsion / (dist * dist) + (dist < minDist ? (minDist - dist) * 0.08 : 0);
      const ux = dx / dist;
      const uy = dy / dist;
      a.vx += ux * force;
      a.vy += uy * force;
      b.vx -= ux * force;
      b.vy -= uy * force;
    }
  }

  for (const link of links) {
    const a = byId.get(link.source);
    const b = byId.get(link.target);
    if (!a || !b) continue;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const dist = Math.hypot(dx, dy) || 0.01;
    const pull = (dist - rest) * spring;
    const ux = dx / dist;
    const uy = dy / dist;
    a.vx += ux * pull;
    a.vy += uy * pull;
    b.vx -= ux * pull;
    b.vy -= uy * pull;
  }

  for (const node of nodes) {
    if (node.id === pinnedId) {
      node.vx = 0;
      node.vy = 0;
      continue;
    }
    node.vx += (cx - node.x) * center;
    node.vy += (cy - node.y) * center;
    node.vx *= damp;
    node.vy *= damp;
    node.x += node.vx;
    node.y += node.vy;
    // Kept inside the view, so a page cannot be pushed somewhere it cannot be
    // reached by scrolling. The margin is what the drawing needs beyond the page's
    // own circle: a name under it, a count beside it. Without it those are drawn
    // outside the view for a page held against an edge.
    const padX = node.radius + 6 + margin.x;
    const padY = node.radius + 6 + margin.y;
    node.x = Math.min(width - padX, Math.max(padX, node.x));
    node.y = Math.min(height - padY, Math.max(padY, node.y));
  }
}

/**
 * How much the graph is still moving, averaged over the pages.
 *
 * The drawing loop stops when this is small rather than after a fixed number of
 * steps: a settled graph costs nothing to leave alone, and a big one gets as many
 * steps as it needs.
 */
export function averageSpeed(nodes: ForceNode[]): number {
  if (nodes.length === 0) return 0;
  let total = 0;
  for (const node of nodes) total += Math.hypot(node.vx, node.vy);
  return total / nodes.length;
}
