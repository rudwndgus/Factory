"use client";
import { useEffect, useRef } from "react";
import type { Employee } from "../lib/api";
import { roles } from "../lib/api";
type Props = {
  employees: Employee[];
  reports: number;
  onSelect: (role: string) => void;
  connected: boolean;
  mode?: string;
  teamWorking?: boolean;
  blocked?: boolean;
};
export default function OfficeScene(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const gameRef = useRef<import("phaser").Game | null>(null);
  const current = useRef(props);
  current.current = props;
  useEffect(() => {
    let game: import("phaser").Game | undefined;
    let disposed = false;
    import("phaser").then(({ default: P }) => {
      if (disposed || !host.current) return;
      class Office extends P.Scene {
        people: {
          sprite: import("phaser").GameObjects.Container;
          light: import("phaser").GameObjects.Arc;
          label: import("phaser").GameObjects.Text;
          role: string;
          homeX: number;
          homeY: number;
        }[] = [];
        paper!: import("phaser").GameObjects.Text;
        create() {
          const g = this.add.graphics();
          const rect = (
            x: number,
            y: number,
            w: number,
            h: number,
            c: number,
          ) => {
            g.fillStyle(c);
            g.fillRect(x, y, w, h);
          };
          rect(0, 0, 1000, 690, 0xe6eddf);
          rect(26, 26, 948, 628, 0x566856);
          rect(34, 34, 932, 612, 0xa3b79a);
          rect(42, 62, 916, 576, 0xf0e4cf);
          for (let y = 65; y < 630; y += 24)
            for (let x = 44; x < 955; x += 48) {
              rect(
                x,
                y,
                46,
                22,
                (Math.floor(y / 24) + Math.floor(x / 48)) % 2
                  ? 0xe9dbc3
                  : 0xeee0c9,
              );
              rect(x, y + 21, 46, 1, 0xdfceb3);
            }
          // Flat top-down architecture, original procedural furniture and pixel characters.
          rect(42, 60, 916, 14, 0x769478);
          rect(42, 42, 916, 20, 0xc4d3b5);
          for (const x of [106, 380, 702]) {
            rect(x, 39, 116, 29, 0x688b7b);
            rect(x + 5, 42, 106, 21, 0xbce0dc);
            rect(x + 56, 42, 4, 21, 0xe7eee0);
          }
          rect(483, 76, 12, 192, 0x83967b);
          rect(486, 76, 6, 186, 0xb9c8a7);
          rect(483, 336, 12, 185, 0x83967b);
          rect(43, 280, 348, 12, 0x93a283);
          rect(601, 280, 354, 12, 0x93a283);
          rect(45, 493, 346, 12, 0x93a283);
          rect(601, 493, 350, 12, 0x93a283);
          const label = (x: number, y: number, t: string) =>
            this.add.text(x, y, t, {
              fontFamily: "monospace",
              fontSize: "11px",
              fontStyle: "bold",
              color: "#5e6d52",
              letterSpacing: 2,
            });
          label(62, 87, "01 / DISCOVERY LAB");
          label(520, 87, "02 / STORY STUDIO");
          label(62, 310, "03 / PRODUCTION");
          label(520, 310, "04 / FINISHING ROOM");
          label(60, 525, "BREAK ROOM");
          label(678, 525, "CEO OFFICE");
          const plant = (x: number, y: number) => {
            rect(x + 3, y + 18, 17, 15, 0xb88763);
            rect(x + 6, y + 30, 11, 3, 0x886349);
            rect(x + 10, y, 5, 23, 0x49704c);
            rect(x, y + 3, 13, 10, 0x608d55);
            rect(x + 13, y + 7, 12, 9, 0x80a566);
            rect(x + 3, y - 2, 14, 9, 0x759957);
          };
          [
            [60, 115],
            [442, 112],
            [907, 112],
            [448, 426],
            [61, 425],
            [910, 425],
            [628, 562],
            [907, 575],
          ].forEach(([x, y]) => plant(x, y));
          const bookshelf = (x: number, y: number) => {
            rect(x, y, 78, 32, 0x9b7352);
            for (let i = 0; i < 9; i++)
              rect(
                x + 5 + i * 8,
                y + 4,
                5,
                19,
                [0xd08070, 0x719aa2, 0xc6aa6c, 0x8b9a70][i % 4],
              );
            rect(x, y + 25, 78, 6, 0xb18b66);
          };
          bookshelf(260, 112);
          bookshelf(772, 115);
          bookshelf(280, 336);
          const desk = (x: number, y: number) => {
            rect(x - 37, y - 18, 78, 48, 0x967657);
            rect(x - 39, y - 21, 80, 43, 0xd1aa7d);
            rect(x - 35, y - 18, 72, 35, 0xe1bd90);
            rect(x - 22, y - 12, 39, 24, 0x4b5960);
            rect(x - 18, y - 9, 31, 17, 0x86b9b2);
            rect(x - 3, y + 11, 5, 5, 0x566869);
            rect(x - 18, y + 18, 28, 5, 0xf3e8cf);
            rect(x + 25, y - 5, 9, 10, 0xf4e9cd);
            rect(x + 28, y - 9, 3, 4, 0x8f6e51);
            rect(x - 14, y + 40, 31, 25, 0x708471);
            rect(x - 10, y + 44, 23, 17, 0x91a28a);
          };
          const seats = [
            [170, 157],
            [355, 208],
            [166, 230],
            [597, 157],
            [800, 213],
            [165, 362],
            [354, 431],
            [595, 363],
            [802, 433],
            [597, 439],
          ];
          seats.forEach(([x, y], i) => {
            desk(x, y);
            const role = roles[i];
            const sprite = this.add.container(x, y + 40);
            const body = this.add.graphics();
            const rr = (
              a: number,
              b: number,
              w: number,
              h: number,
              c: number,
            ) => {
              body.fillStyle(c);
              body.fillRect(a, b, w, h);
            };
            rr(-11, 13, 23, 6, 0x000000);
            body.setAlpha(1);
            rr(-9, -13, 18, 17, 0xeac39a);
            rr(
              -11,
              -16,
              22,
              8,
              [0x534137, 0x724e37, 0x544b55, 0x9b7453][i % 4],
            );
            rr(-11, -9, 5, 9, 0x604a38);
            rr(
              -8,
              2,
              17,
              13,
              [0x728baa, 0x7d9f73, 0xb77869, 0xb59b61, 0x8e80a5][i % 5],
            );
            rr(-8, 13, 6, 7, 0x495462);
            rr(3, 13, 6, 7, 0x495462);
            rr(-13, 3, 5, 8, 0xeac39a);
            rr(9, 3, 5, 8, 0xeac39a);
            rr(-4, -5, 3, 3, 0x403b34);
            rr(5, -5, 3, 3, 0x403b34);
            sprite.add(body);
            const hit = this.add
              .zone(0, 0, 60, 62)
              .setInteractive({ useHandCursor: true });
            sprite.add(hit);
            hit.on("pointerdown", () => {
              if (!current.current.blocked) current.current.onSelect(role);
            });
            const light = this.add.circle(x + 34, y - 19, 4, 0x8d9b82);
            const text = this.add
              .text(x, y + 70, role.replace(" / ", "/"), {
                fontFamily: "monospace",
                fontSize: "8px",
                color: "#60664f",
              })
              .setOrigin(0.5);
            this.people.push({
              sprite,
              light,
              label: text,
              role,
              homeX: sprite.x,
              homeY: sprite.y,
            });
          });
          // Sofa, coffee machine, rugs and CEO desk.
          rect(91, 560, 153, 45, 0x698c80);
          rect(88, 552, 159, 17, 0x82a392);
          rect(86, 558, 13, 50, 0x739684);
          rect(236, 558, 13, 50, 0x739684);
          rect(111, 561, 53, 29, 0x9ab2a0);
          rect(171, 561, 53, 29, 0x9ab2a0);
          rect(283, 556, 45, 44, 0xbc9671);
          rect(288, 552, 35, 24, 0x687371);
          rect(295, 556, 21, 11, 0x3f4f4c);
          rect(305, 574, 9, 8, 0xf9ead1);
          rect(395, 548, 214, 64, 0xbac6a2);
          for (let x = 405; x < 600; x += 12) rect(x, 551, 4, 58, 0xadba96);
          rect(706, 559, 143, 57, 0x947555);
          rect(701, 554, 151, 52, 0xd1ac7e);
          rect(709, 559, 136, 42, 0xe5c79f);
          rect(751, 555, 44, 27, 0x62726d);
          rect(756, 559, 34, 17, 0x8fb7a3);
          this.paper = this.add
            .text(718, 582, "▤", {
              fontFamily: "monospace",
              fontSize: "30px",
              color: "#fff9e7",
            })
            .setInteractive({ useHandCursor: true });
          this.paper.on("pointerdown", () => {
            if (!current.current.blocked) current.current.onSelect("CEO");
          });
          this.add
            .text(500, 660, "PIXEL SHORTS FACTORY  /  OFFICE 01", {
              fontFamily: "monospace",
              fontSize: "10px",
              color: "#7c8d75",
              letterSpacing: 3,
            })
            .setOrigin(0.5);
          this.input.on(
            "wheel",
            (_p: unknown, _g: unknown, _dx: number, dy: number) => {
              this.cameras.main.setZoom(
                P.Math.Clamp(this.cameras.main.zoom - dy * 0.001, 1, 2),
              );
            },
          );
          let down = false;
          this.input.on("pointerdown", () => (down = true));
          this.input.on("pointerup", () => (down = false));
          this.input.on("pointermove", (p: import("phaser").Input.Pointer) => {
            if (down && this.cameras.main.zoom > 1) {
              this.cameras.main.scrollX -=
                (p.x - p.prevPosition.x) / this.cameras.main.zoom;
              this.cameras.main.scrollY -=
                (p.y - p.prevPosition.y) / this.cameras.main.zoom;
            }
          });
          this.cameras.main.setBounds(0, 0, 1000, 690);
        }
        update(t: number) {
          for (const p of this.people) {
            const state = current.current.employees.find(
              (e) => e.role === p.role,
            );
            const active =
              current.current.connected && !!current.current.teamWorking;
            const assigned = state?.status === "WORKING";
            p.light.setFillStyle(
              active
                ? 0x69ae76
                : current.current.connected
                  ? 0xa4af91
                  : 0xadb7ac,
            );
            p.sprite.setAngle(active ? Math.sin(t / 110) * 6 : 0);
            p.sprite.setPosition(
              p.homeX + (active ? Math.sin(t / 650) * 16 : 0),
              p.homeY + (active ? Math.sin(t / 130) * 3 : 0),
            );
            p.light.setAlpha(active ? 0.65 + Math.sin(t / 180) * 0.35 : 1);
            p.label.setColor(
              assigned ? "#1f6b3d" : active ? "#537848" : "#60664f",
            );
          }
          if (this.paper)
            this.paper.setAlpha(current.current.reports ? 1 : 0.4);
        }
      }
      game = new P.Game({
        type: P.AUTO,
        parent: host.current,
        width: 1000,
        height: 690,
        backgroundColor: "#e6eddf",
        pixelArt: true,
        antialias: false,
        scene: Office,
        scale: { mode: P.Scale.FIT, autoCenter: P.Scale.CENTER_BOTH },
        audio: { noAudio: true },
      });
      gameRef.current = game;
    });
    return () => {
      disposed = true;
      game?.destroy(true);
    };
  }, []);
  function zoom(amount: number) {
    const camera = gameRef.current?.scene.getScenes(true)[0]?.cameras.main;
    if (camera)
      camera.setZoom(Math.max(1, Math.min(2.8, camera.zoom + amount)));
  }
  return (
    <div className="scene-container">
      <div className="map-zoom">
        <button aria-label="Zoom in office" onClick={() => zoom(0.4)}>
          +
        </button>
        <button aria-label="Zoom out office" onClick={() => zoom(-0.4)}>
          −
        </button>
      </div>
      <div
        className="office-canvas"
        ref={host}
        aria-label="Interactive pixel office. Use the employee list below for keyboard access."
      />
    </div>
  );
}
