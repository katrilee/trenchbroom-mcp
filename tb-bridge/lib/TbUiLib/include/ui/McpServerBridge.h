/*
 Copyright (C) 2026 TrenchBroom MCP Bridge contributors

 This file is part of TrenchBroom.

 TrenchBroom is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 TrenchBroom is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with TrenchBroom. If not, see <http://www.gnu.org/licenses/>.
 */

#pragma once

#include "ui/McpNodeIndex.h"

#include <QByteArray>
#include <QDateTime>
#include <QFileSystemWatcher>
#include <QJsonObject>
#include <QObject>
#include <QTcpServer>
#include <QTimer>

#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

class QTcpSocket;

namespace tb::mdl
{
class Map;
class Node;
} // namespace tb::mdl

namespace tb::ui
{

class MapDocument;
class MapWindow;

struct McpError
{
  int code;
  std::string message;
};

/**
 * TCP / JSON-RPC bridge embedded in TrenchBroom's MapWindow (v2026.2 layout).
 *
 * Wire protocol: newline-delimited JSON (NDJSON), one JSON-RPC 2.0 request per
 * line and one response per line. Notifications (no "id") are executed but not
 * answered.
 *
 * Threading: QTcpServer lives on the GUI thread, but every handler runs from
 * QMetaObject::invokeMethod(..., Qt::QueuedConnection), so MapDocument and the
 * GL contexts are only ever touched on the GUI thread and a long RPC (CSG,
 * framebuffer capture) can never re-enter the socket parser.
 *
 * Undo: every mutating handler opens mdl::Transaction. TrenchBroom nests
 * transactions and merges them on commit, so one RPC is one undo step even when
 * it calls helpers that open their own transaction.
 */
class McpServerBridge : public QObject
{
  Q_OBJECT

public:
  static constexpr quint16 DefaultPort = 6161;
  static constexpr int ProtocolVersion = 2;

  using Handler = std::function<QJsonObject(const QJsonObject&)>;

  explicit McpServerBridge(MapWindow* window, QObject* parent = nullptr);
  ~McpServerBridge() override;

  bool start(quint16 port = DefaultPort);
  void stop();
  bool isListening() const;
  quint16 port() const;

  void setAuthToken(std::string token);

  QJsonObject info() const;
  QJsonObject dispatch(const std::string& method, const QJsonObject& params);

  static McpServerBridge* instance();

  /** Storage for the singleton; assigned in the constructor. */
  static McpServerBridge*& instanceRef();
  static McpServerBridge* attachTo(MapWindow& window);

  /**
   * Starts the singleton with no window attached.
   *
   * TrenchBroom opens a Welcome window before any MapWindow exists, so a
   * bridge attached only to MapWindow would not listen until the user opens
   * a map — which is exactly what an agent cannot do. AppController starts
   * it here; MapWindow::attachTo() retargets it later.
   */
  static McpServerBridge* startGlobal(QObject* parent);

private slots:
  void onNewConnection();
  void onReadyRead();
  void onSocketDisconnected();

private:
  struct Connection
  {
    QTcpSocket* socket = nullptr;
    QByteArray buffer;
    std::deque<QByteArray> pending;
    bool scheduled = false;
  };

  void handleLine(Connection& connection, const QByteArray& line);
  QJsonObject execute(const QJsonObject& request);
  void send(Connection& connection, const QJsonObject& response);

  MapWindow& requireWindow() const;
  MapDocument& document() const;
  mdl::Map& map() const;

  void registerHandlers();

  // document / lifecycle
  QJsonObject docNewOrOpen(const QJsonObject& p);
  QJsonObject docState(const QJsonObject& p);
  QJsonObject docSave(const QJsonObject& p);
  QJsonObject docUndo(const QJsonObject& p);
  QJsonObject docRedo(const QJsonObject& p);
  QJsonObject docClearSelection(const QJsonObject& p);
  QJsonObject docSelectById(const QJsonObject& p);
  QJsonObject docSelectByBBox(const QJsonObject& p);
  QJsonObject docSelectAll(const QJsonObject& p);
  QJsonObject docSelection(const QJsonObject& p);
  QJsonObject docListNodeIds(const QJsonObject& p);
  QJsonObject docSetMods(const QJsonObject& p);

  // geometry / CSG
  QJsonObject geoCreateCuboid(const QJsonObject& p);
  QJsonObject geoCreateBrushFromPoints(const QJsonObject& p);
  QJsonObject geoCreateRoomHollow(const QJsonObject& p);
  QJsonObject geoCsgSubtract(const QJsonObject& p);
  QJsonObject geoCsgUnion(const QJsonObject& p);
  QJsonObject geoCsgIntersect(const QJsonObject& p);
  QJsonObject geoClipBrush(const QJsonObject& p);
  QJsonObject geoTransform(const QJsonObject& p);
  QJsonObject geoDeleteSelected(const QJsonObject& p);
  QJsonObject geoDuplicateSelected(const QJsonObject& p);

  // materials
  QJsonObject matLoadCollection(const QJsonObject& p);
  QJsonObject matList(const QJsonObject& p);
  QJsonObject matApply(const QJsonObject& p);
  QJsonObject matSetProperties(const QJsonObject& p);

  // entities
  QJsonObject entCreatePoint(const QJsonObject& p);
  QJsonObject entCreateBrush(const QJsonObject& p);
  QJsonObject entSetProperty(const QJsonObject& p);
  QJsonObject entList(const QJsonObject& p);
  QJsonObject prefabPaste(const QJsonObject& p);

  // view / capture
  QJsonObject viewCapture3D(const QJsonObject& p);
  QJsonObject viewCaptureOrtho(const QJsonObject& p);
  QJsonObject viewLoadPointFile(const QJsonObject& p);
  QJsonObject viewSetLayout(const QJsonObject& p);
  QJsonObject viewCameraInfo(const QJsonObject& p);

  // validation
  QJsonObject valGetIssues(const QJsonObject& p);

  // organisation
  QJsonObject layerCreate(const QJsonObject& p);
  QJsonObject layerSetActive(const QJsonObject& p);
  QJsonObject layerList(const QJsonObject& p);
  QJsonObject groupCreate(const QJsonObject& p);
  QJsonObject groupUngroup(const QJsonObject& p);

  // native sculpting (2026.2 free functions)
  QJsonObject geoGetBrushTopology(const QJsonObject& p);
  QJsonObject geoMoveVertices(const QJsonObject& p);
  QJsonObject geoMoveEdges(const QJsonObject& p);
  QJsonObject geoMoveFaces(const QJsonObject& p);
  QJsonObject geoExtrudeFaces(const QJsonObject& p);
  QJsonObject geoAddVertex(const QJsonObject& p);
  QJsonObject geoRemoveVertices(const QJsonObject& p);
  QJsonObject geoSnapVertices(const QJsonObject& p);
  QJsonObject geoShear(const QJsonObject& p);
  QJsonObject geoFlip(const QJsonObject& p);
  QJsonObject geoDuplicateAndTransform(const QJsonObject& p);

  QJsonObject texFitTexture(const QJsonObject& p);
  QJsonObject texAlignToFace(const QJsonObject& p);
  QJsonObject texAlignToWorld(const QJsonObject& p);
  QJsonObject texCopyUvBetweenFaces(const QJsonObject& p);

  QJsonObject selSelectTouching(const QJsonObject& p);
  QJsonObject selSelectInside(const QJsonObject& p);

  QJsonObject viewIsolateSelection(const QJsonObject& p);
  QJsonObject viewHideSelection(const QJsonObject& p);
  QJsonObject viewUnhideAll(const QJsonObject& p);
  QJsonObject viewSetRenderMode(const QJsonObject& p);

  // snapshots + auto reload
  QJsonObject snapshotSave(const QJsonObject& p);
  QJsonObject snapshotList(const QJsonObject& p);
  QJsonObject snapshotRestore(const QJsonObject& p);
  void onWatchedFileChanged(const QString& path);
  void armFileWatcher(const std::string& path);
  void noteOwnWrite(const std::string& path);
  std::string snapshotDir() const;

  // helpers
  std::vector<std::string> stampIds(const std::vector<mdl::Node*>& nodes);
  std::vector<mdl::Node*> addToCurrentParent(const std::vector<mdl::Node*>& nodes);
  void selectOnly(const std::vector<mdl::Node*>& nodes);
  QJsonObject nodeSummary(mdl::Node* node) const;

  MapWindow* m_window = nullptr;
  std::string m_authToken;
  QTcpServer m_server;
  std::vector<std::unique_ptr<Connection>> m_connections;
  std::map<std::string, Handler> m_handlers;
  McpNodeIndex m_index;

  QFileSystemWatcher m_watcher;
  QString m_watchedPath;
  QDateTime m_lastKnownMtime;
  bool m_suppressReload = false;
};

} // namespace tb::ui
