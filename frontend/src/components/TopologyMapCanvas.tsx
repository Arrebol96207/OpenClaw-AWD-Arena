import React, { useEffect, useRef } from 'react'
import { Network } from 'vis-network'
import type { Data, Edge, Node, Options } from 'vis-network'

export interface TopologyMapProps {
  playerCount: number
  phase: string
}

const TopologyMapCanvas: React.FC<TopologyMapProps> = ({ playerCount, phase }) => {
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const nodes: Node[] = []
    const edges: Edge[] = []
    const isAttack = phase === 'attack' || phase === 'finished'

    nodes.push({
      id: 'arena',
      label: isAttack ? 'Arena\nNetwork' : 'Referee\nEngine',
      shape: 'hexagon',
      color: {
        background: isAttack ? '#ef4444' : '#3b82f6',
        border: '#1e293b',
      },
      font: { color: '#ffffff', face: 'monospace' },
      size: 30,
    })

    for (let i = 1; i <= playerCount; i++) {
      nodes.push({
        id: `agent_${i}`,
        label: `Agent ${i}`,
        shape: 'box',
        color: {
          background: '#0ea5e9',
          border: '#0284c7',
        },
        font: { color: '#ffffff', face: 'monospace' },
      })

      nodes.push({
        id: `target_${i}`,
        label: `Target ${i}`,
        shape: 'database',
        color: {
          background: '#10b981',
          border: '#059669',
        },
        font: { color: '#ffffff', face: 'monospace' },
      })

      if (isAttack) {
        edges.push({
          from: `agent_${i}`,
          to: 'arena',
          color: { color: '#ef4444' },
          dashes: true,
        })
        edges.push({
          from: `target_${i}`,
          to: 'arena',
          color: { color: '#94a3b8' },
        })
      } else {
        nodes.push({
          id: `switch_${i}`,
          label: `Net ${i}`,
          shape: 'dot',
          size: 10,
          color: { background: '#64748b', border: '#475569' },
          font: { color: '#94a3b8', size: 12 },
        })

        edges.push({
          from: `agent_${i}`,
          to: `switch_${i}`,
          color: { color: '#0ea5e9' },
        })
        edges.push({
          from: `target_${i}`,
          to: `switch_${i}`,
          color: { color: '#10b981' },
        })
      }
    }

    const data: Data = { nodes, edges }
    const options: Options = {
      physics: {
        enabled: true,
        barnesHut: {
          gravitationalConstant: -2000,
          centralGravity: 0.3,
          springLength: 100,
          springConstant: 0.04,
          damping: 0.09,
          avoidOverlap: 0.1,
        },
      },
      interaction: {
        zoomView: true,
        dragView: true,
      },
      edges: {
        width: 2,
        smooth: {
          enabled: true,
          type: 'continuous',
          roundness: 0.35,
        },
      },
    }

    if (networkRef.current) {
      networkRef.current.setData(data)
    } else {
      networkRef.current = new Network(containerRef.current, data, options)
    }
  }, [playerCount, phase])

  useEffect(() => {
    return () => {
      networkRef.current?.destroy()
      networkRef.current = null
    }
  }, [])

  return <div ref={containerRef} className="absolute inset-0" />
}

export default TopologyMapCanvas
